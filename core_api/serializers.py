from decimal import Decimal
from urllib.parse import urlparse

from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers

from .models import (AcceptanceCriterion, CriterionResult, Milestone, Project, ProjectApplication,
                     Submission, User, Wallet)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'role', 'pfi_score']
        read_only_fields = ['pfi_score']
        extra_kwargs = {'password': {'write_only': True}}

    def validate(self, attrs):
        validate_password(attrs['password'], User(username=attrs.get('username', ''), email=attrs.get('email', '')))
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class CriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcceptanceCriterion
        fields = ['id', 'description', 'weight']


class MilestoneSerializer(serializers.ModelSerializer):
    criteria = CriterionSerializer(many=True, read_only=True)

    class Meta:
        model = Milestone
        fields = ['id', 'project', 'title', 'description', 'payment', 'status', 'escrow_balance',
                  'paid_amount', 'order', 'criteria']
        read_only_fields = fields


class ProjectSerializer(serializers.ModelSerializer):
    milestones = MilestoneSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = ['id', 'client', 'title', 'description', 'budget', 'deadline', 'status', 'created_at', 'milestones']
        read_only_fields = ['client', 'status', 'created_at', 'milestones']

    def validate_budget(self, v):
        if v <= Decimal('0'):
            raise serializers.ValidationError('Budget must be positive.')
        return v

    def validate_deadline(self, v):
        if v <= timezone.now():
            raise serializers.ValidationError('Deadline must be in the future.')
        return v


class ApplicationSerializer(serializers.ModelSerializer):
    freelancer_name = serializers.CharField(source='freelancer.username', read_only=True)
    freelancer_pfi = serializers.FloatField(source='freelancer.pfi_score', read_only=True)

    class Meta:
        model = ProjectApplication
        fields = ['id', 'project', 'freelancer', 'freelancer_name', 'freelancer_pfi', 'cover_note', 'status', 'created_at']
        read_only_fields = ['id', 'project', 'freelancer', 'freelancer_name', 'freelancer_pfi', 'status', 'created_at']


class CriterionResultSerializer(serializers.ModelSerializer):
    description = serializers.CharField(source='criterion.description', read_only=True)
    weight = serializers.DecimalField(source='criterion.weight', max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = CriterionResult
        fields = ['criterion', 'description', 'weight', 'ai_passed', 'ai_note', 'client_passed']


class SubmissionSerializer(serializers.ModelSerializer):
    results = CriterionResultSerializer(many=True, read_only=True)
    freelancer_name = serializers.CharField(source='freelancer.username', read_only=True)
    freelancer_pfi = serializers.FloatField(source='freelancer.pfi_score', read_only=True)

    class Meta:
        model = Submission
        fields = ['id', 'milestone', 'freelancer', 'freelancer_name', 'freelancer_pfi', 'github_link', 'notes', 'status',
                  'ai_available', 'ai_score', 'ai_summary', 'payout_amount', 'payout_fraction',
                  'created_at', 'decided_at', 'results']
        read_only_fields = [f for f in fields if f not in ('github_link', 'notes')]

    def validate_github_link(self, v):
        p = urlparse(v)
        parts = [x for x in p.path.split('/') if x]
        if p.scheme != 'https' or p.netloc.lower() not in ('github.com', 'www.github.com') or len(parts) < 2:
            raise serializers.ValidationError('Provide an https://github.com/<owner>/<repo> link.')
        return v


class DecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['accept', 'request_revision', 'reject'])
    passed_criteria = serializers.ListField(child=serializers.IntegerField(), required=False)
    feedback = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ['balance']
