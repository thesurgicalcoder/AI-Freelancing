import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class User(AbstractUser):
    class Role(models.TextChoices):
        CLIENT = 'Client', 'Client'
        FREELANCER = 'Freelancer', 'Freelancer'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.FREELANCER)
    # Profile Factor Index (0-100): recomputed from real decided submissions, never set by users.
    pfi_score = models.FloatField(default=0.0)


class Wallet(models.Model):
    """Spendable balance. Changes ONLY through services.py, always with a LedgerEntry."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(balance__gte=0), name='wallet_balance_non_negative')]


@receiver(post_save, sender=User)
def create_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(user=instance)


class Project(models.Model):
    class Status(models.TextChoices):
        OPEN = 'Open', 'Open'
        IN_PROGRESS = 'In Progress', 'In Progress'
        COMPLETED = 'Completed', 'Completed'
        CANCELLED = 'Cancelled', 'Cancelled'

    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    title = models.CharField(max_length=255)
    description = models.TextField()
    budget = models.DecimalField(max_digits=10, decimal_places=2)
    deadline = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)


class Milestone(models.Model):
    class Status(models.TextChoices):
        PENDING = 'Pending', 'Pending funding'
        FUNDED = 'Funded', 'Funded (in escrow)'
        COMPLETED = 'Completed', 'Completed'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    payment = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    escrow_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']


class AcceptanceCriterion(models.Model):
    """One measurable deliverable inside a milestone. Weights of a milestone sum to 100."""
    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='criteria')
    description = models.CharField(max_length=500)
    weight = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        ordering = ['id']


class ProjectApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = 'Pending', 'Pending'
        APPROVED = 'Approved', 'Approved'
        REJECTED = 'Rejected', 'Rejected'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='applications')
    freelancer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='applications')
    cover_note = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'freelancer'], name='one_application_per_freelancer')]


class Submission(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = 'Submitted', 'Submitted'
        ACCEPTED = 'Accepted', 'Accepted'
        REVISION_REQUESTED = 'Revision Requested', 'Revision Requested'
        REJECTED = 'Rejected', 'Rejected'

    milestone = models.ForeignKey(Milestone, on_delete=models.CASCADE, related_name='submissions')
    freelancer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submissions')
    github_link = models.URLField()
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.SUBMITTED)
    # AI review is decision support only. null = not analysed / service unavailable.
    ai_score = models.FloatField(null=True, blank=True)
    ai_summary = models.TextField(blank=True)
    ai_available = models.BooleanField(default=False)
    payout_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payout_fraction = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['milestone', 'freelancer'], name='one_submission_per_freelancer_milestone')]


class CriterionResult(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='results')
    criterion = models.ForeignKey(AcceptanceCriterion, on_delete=models.CASCADE)
    ai_passed = models.BooleanField(null=True, blank=True)
    ai_note = models.CharField(max_length=500, blank=True)
    client_passed = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['submission', 'criterion'], name='one_result_per_criterion')]


class LedgerEntry(models.Model):
    """Append-only money log. Entries sharing a txn_id sum to zero (wallet <-> escrow)."""
    class Kind(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        ESCROW_FUND = 'escrow_fund', 'Escrow funded'
        PAYOUT = 'payout', 'Payout to freelancer'
        REFUND = 'refund', 'Refund to client'

    txn_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    wallet = models.ForeignKey(Wallet, null=True, blank=True, on_delete=models.PROTECT, related_name='entries')  # null = escrow side
    milestone = models.ForeignKey(Milestone, null=True, blank=True, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # signed from the perspective of `wallet`/escrow
    created_at = models.DateTimeField(auto_now_add=True)
