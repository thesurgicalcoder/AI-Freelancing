from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate

from . import services
from .models import Milestone, Project, ProjectApplication, Submission, User, Wallet
from .permissions import IsClient, IsFreelancer
from .serializers import (ApplicationSerializer, DecisionSerializer, MilestoneSerializer, ProjectSerializer,
                          SubmissionSerializer, UserSerializer, WalletSerializer)


def domain_error_response(e):
    return Response({'error': e.message}, status=e.status)


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {'token': str(refresh.access_token), 'access': str(refresh.access_token), 'refresh': str(refresh)}


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = UserSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        return Response({**tokens_for(user), 'id': user.id, 'role': user.role, 'user': s.data}, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user = authenticate(username=request.data.get('username'), password=request.data.get('password'))
        if not user:
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({**tokens_for(user), 'role': user.role, 'id': user.id})


# ---------- projects ----------

class ProjectListCreateView(generics.ListCreateAPIView):
    serializer_class = ProjectSerializer

    def get_permissions(self):
        return [IsClient()] if self.request.method == 'POST' else super().get_permissions()

    def get_queryset(self):
        u = self.request.user
        qs = Project.objects.prefetch_related('milestones__criteria').order_by('-id')
        if u.role == User.Role.CLIENT:
            return qs.filter(client=u)
        # Freelancers: the open marketplace plus projects they were approved on.
        return qs.filter(Q(status=Project.Status.OPEN) |
                         Q(applications__freelancer=u, applications__status=ProjectApplication.Status.APPROVED)).distinct()

    def perform_create(self, serializer):
        with transaction.atomic():
            project = serializer.save(client=self.request.user)
            services.generate_milestones(project)


class ProjectDetailView(generics.RetrieveAPIView):
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return ProjectListCreateView.get_queryset(self)


class MilestoneListView(generics.ListAPIView):
    serializer_class = MilestoneSerializer

    def get_queryset(self):
        project = get_object_or_404(ProjectListCreateView.get_queryset(self), pk=self.kwargs['project_id'])
        return project.milestones.prefetch_related('criteria')


# ---------- applications (client chooses who may submit) ----------

class ProjectApplyView(APIView):
    permission_classes = [IsFreelancer]

    def post(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id)
        if project.status not in (Project.Status.OPEN, Project.Status.IN_PROGRESS):
            return Response({'error': 'This project is not accepting applications.'}, status=409)
        app, created = ProjectApplication.objects.get_or_create(
            project=project, freelancer=request.user, defaults={'cover_note': request.data.get('cover_note', '')})
        if not created:
            return Response({'error': 'You have already applied to this project.'}, status=400)
        return Response(ApplicationSerializer(app).data, status=201)


class ProjectApplicationsView(generics.ListAPIView):
    """Client sees applicants ranked by PFI."""
    serializer_class = ApplicationSerializer
    permission_classes = [IsClient]

    def get_queryset(self):
        project = get_object_or_404(Project, pk=self.kwargs['project_id'], client=self.request.user)
        return project.applications.select_related('freelancer').order_by('-freelancer__pfi_score', 'created_at')


class ApplicationDecideView(APIView):
    permission_classes = [IsClient]

    def post(self, request, pk):
        app = get_object_or_404(ProjectApplication, pk=pk, project__client=request.user)
        action = request.data.get('action')
        if action not in ('approve', 'reject'):
            return Response({'error': "action must be 'approve' or 'reject'."}, status=400)
        app.status = ProjectApplication.Status.APPROVED if action == 'approve' else ProjectApplication.Status.REJECTED
        app.save(update_fields=['status'])
        return Response(ApplicationSerializer(app).data)


# ---------- wallet & escrow ----------

class WalletView(APIView):
    def get(self, request):
        return Response(WalletSerializer(Wallet.objects.get(user=request.user)).data)


class DepositView(APIView):
    """Test-mode top-up. Disabled unless ALLOW_TEST_DEPOSIT; replaced by payment-provider webhooks in Phase 3."""

    def post(self, request):
        if not getattr(settings, 'ALLOW_TEST_DEPOSIT', settings.DEBUG):
            return Response({'error': 'Deposits are disabled.'}, status=403)
        try:
            wallet = services.deposit(request.user, request.data.get('amount', 0))
        except services.DomainError as e:
            return domain_error_response(e)
        except Exception:
            return Response({'error': 'Invalid amount.'}, status=400)
        return Response(WalletSerializer(wallet).data)


class FundMilestoneView(APIView):
    permission_classes = [IsClient]

    def post(self, request, pk):
        try:
            ms = services.fund_milestone(pk, request.user)
        except Milestone.DoesNotExist:
            return Response({'error': 'Not found.'}, status=404)
        except services.DomainError as e:
            return domain_error_response(e)
        return Response(MilestoneSerializer(ms).data)


# ---------- submissions ----------

class MilestoneSubmitView(APIView):
    permission_classes = [IsFreelancer]

    def post(self, request, pk):
        milestone = get_object_or_404(Milestone.objects.select_related('project'), pk=pk)
        if not services.can_submit(milestone.project, request.user):
            return Response({'error': 'The client has not approved you for this project.'}, status=403)
        if milestone.status != Milestone.Status.FUNDED:
            return Response({'error': 'Milestone is not open for submissions (needs to be funded and not completed).'}, status=409)

        existing = Submission.objects.filter(milestone=milestone, freelancer=request.user).first()
        if existing and existing.status != Submission.Status.REVISION_REQUESTED:
            return Response({'error': 'You already have a submission for this milestone.'}, status=409)

        s = SubmissionSerializer(existing, data=request.data, partial=bool(existing))
        s.is_valid(raise_exception=True)
        submission = s.save(milestone=milestone, freelancer=request.user, status=Submission.Status.SUBMITTED)
        services.run_ai_review(submission)
        submission.refresh_from_db()
        return Response(SubmissionSerializer(submission).data, status=201)


class MilestoneSubmissionsView(generics.ListAPIView):
    """Client-only: submissions for a milestone, AI-ranked (unscored last) to support the client's decision."""
    serializer_class = SubmissionSerializer
    permission_classes = [IsClient]

    def get_queryset(self):
        ms = get_object_or_404(Milestone, pk=self.kwargs['pk'], project__client=self.request.user)
        return (ms.submissions.select_related('freelancer').prefetch_related('results__criterion')
                .order_by(F('ai_score').desc(nulls_last=True), 'created_at'))


class SubmissionDecideView(APIView):
    permission_classes = [IsClient]

    def post(self, request, pk):
        d = DecisionSerializer(data=request.data)
        d.is_valid(raise_exception=True)
        try:
            sub = services.decide_submission(pk, request.user, d.validated_data['action'],
                                             d.validated_data.get('passed_criteria'), d.validated_data.get('feedback', ''))
        except Submission.DoesNotExist:
            return Response({'error': 'Not found.'}, status=404)
        except services.DomainError as e:
            return domain_error_response(e)
        return Response(SubmissionSerializer(sub).data)


class MySubmissionsView(generics.ListAPIView):
    serializer_class = SubmissionSerializer
    permission_classes = [IsFreelancer]

    def get_queryset(self):
        return Submission.objects.filter(freelancer=self.request.user).prefetch_related('results__criterion').order_by('-id')
