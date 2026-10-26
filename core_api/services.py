"""Business logic. Every money movement lives here, inside a DB transaction, with a ledger trail."""
import uuid
from decimal import Decimal, ROUND_DOWN

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Avg
from django.utils import timezone

from .models import (AcceptanceCriterion, CriterionResult, LedgerEntry, Milestone, Project,
                     ProjectApplication, Submission, User, Wallet)

CENT = Decimal('0.01')


class DomainError(Exception):
    """A business-rule violation that should surface as HTTP 400/409."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------- wallet / escrow ----------

def deposit(user, amount):
    """Test-mode top-up. Replaced by a payment-provider webhook in Phase 3."""
    amount = Decimal(amount).quantize(CENT)
    if amount <= 0:
        raise DomainError('Amount must be positive.')
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        wallet.balance += amount
        wallet.save(update_fields=['balance'])
        LedgerEntry.objects.create(kind=LedgerEntry.Kind.DEPOSIT, wallet=wallet, amount=amount)
    return wallet


def fund_milestone(milestone_id, client):
    """Move the milestone's payment from the client's wallet into escrow."""
    with transaction.atomic():
        milestone = Milestone.objects.select_for_update().select_related('project').get(pk=milestone_id)
        if milestone.project.client_id != client.id:
            raise DomainError('Only the project owner can fund this milestone.', 403)
        if milestone.status != Milestone.Status.PENDING:
            raise DomainError('Milestone is already funded or completed.', 409)
        if milestone.project.status in (Project.Status.CANCELLED, Project.Status.COMPLETED):
            raise DomainError('Project is closed.', 409)
        total = sum(c.weight for c in milestone.criteria.all())
        if total != Decimal('100'):
            raise DomainError(f'Acceptance criteria weights must sum to 100 (currently {total}).')

        wallet = Wallet.objects.select_for_update().get(user=client)
        if wallet.balance < milestone.payment:
            raise DomainError('Insufficient wallet balance.', 402)
        wallet.balance -= milestone.payment
        wallet.save(update_fields=['balance'])

        milestone.escrow_balance = milestone.payment
        milestone.status = Milestone.Status.FUNDED
        milestone.save(update_fields=['escrow_balance', 'status'])

        txn = uuid.uuid4()
        LedgerEntry.objects.create(txn_id=txn, kind=LedgerEntry.Kind.ESCROW_FUND, wallet=wallet,
                                   milestone=milestone, amount=-milestone.payment)
        LedgerEntry.objects.create(txn_id=txn, kind=LedgerEntry.Kind.ESCROW_FUND, wallet=None,
                                   milestone=milestone, amount=milestone.payment)
        if milestone.project.status == Project.Status.OPEN:
            milestone.project.status = Project.Status.IN_PROGRESS
            milestone.project.save(update_fields=['status'])
    return milestone


# ---------- applications ----------

def can_submit(project, user):
    return ProjectApplication.objects.filter(
        project=project, freelancer=user, status=ProjectApplication.Status.APPROVED).exists()


# ---------- AI review (decision support only) ----------

def run_ai_review(submission):
    """Ask the AI service which criteria a repo satisfies. Never blocks or auto-pays: failure => manual review."""
    criteria = list(submission.milestone.criteria.all())
    payload = {
        'github_link': submission.github_link,
        'milestone': submission.milestone.title,
        'criteria': [{'id': c.id, 'description': c.description, 'weight': float(c.weight)} for c in criteria],
    }
    try:
        resp = requests.post(f'{settings.AI_SERVICE_URL}/verify-milestone', json=payload,
                             timeout=settings.AI_SERVICE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return
    if not data.get('available'):
        submission.ai_summary = data.get('summary', '')
        submission.save(update_fields=['ai_summary'])
        return
    by_id = {r.get('criterion_id'): r for r in data.get('results', [])}
    with transaction.atomic():
        for c in criteria:
            r = by_id.get(c.id, {})
            CriterionResult.objects.update_or_create(
                submission=submission, criterion=c,
                defaults={'ai_passed': r.get('passed'), 'ai_note': str(r.get('note', ''))[:500]})
        submission.ai_score = data.get('score')
        submission.ai_summary = data.get('summary', '')
        submission.ai_available = True
        submission.save(update_fields=['ai_score', 'ai_summary', 'ai_available'])


# ---------- decisions & payout ----------

def recompute_pfi(user):
    """PFI = average payout fraction (0-100) over this freelancer's decided submissions."""
    decided = Submission.objects.filter(freelancer=user, decided_at__isnull=False)
    avg = decided.aggregate(a=Avg('payout_fraction'))['a']
    user.pfi_score = float(avg * 100) if avg is not None else 0.0
    user.save(update_fields=['pfi_score'])


def decide_submission(submission_id, client, action, passed_criteria_ids=None, feedback=''):
    """
    Client's decision on a submission.
      accept            -> payout = escrow * (sum of weights of passed criteria / 100); remainder refunded.
      request_revision  -> nothing moves; freelancer may resubmit.
      reject            -> submission closed, nothing moves.
    """
    with transaction.atomic():
        submission = (Submission.objects.select_for_update()
                      .select_related('milestone__project', 'freelancer').get(pk=submission_id))
        milestone = Milestone.objects.select_for_update().get(pk=submission.milestone_id)
        if milestone.project.client_id != client.id:
            raise DomainError('Only the project owner can decide on this submission.', 403)
        if submission.status not in (Submission.Status.SUBMITTED,):
            raise DomainError('This submission has already been decided.', 409)
        if milestone.status != Milestone.Status.FUNDED:
            raise DomainError('Milestone is not awaiting a decision (not funded, or already paid).', 409)

        if action == 'request_revision':
            submission.status = Submission.Status.REVISION_REQUESTED
            submission.notes = (submission.notes + f'\n[Client feedback] {feedback}').strip() if feedback else submission.notes
            submission.save(update_fields=['status', 'notes'])
            return submission
        if action == 'reject':
            submission.status = Submission.Status.REJECTED
            submission.payout_fraction = Decimal('0')
            submission.decided_at = timezone.now()
            submission.save(update_fields=['status', 'payout_fraction', 'decided_at'])
            recompute_pfi(submission.freelancer)
            return submission
        if action != 'accept':
            raise DomainError('Unknown action.')

        criteria = {c.id: c for c in milestone.criteria.all()}
        passed_ids = set(passed_criteria_ids or [])
        if not passed_ids <= set(criteria):
            raise DomainError('Unknown criterion id for this milestone.')
        for cid, crit in criteria.items():
            CriterionResult.objects.update_or_create(
                submission=submission, criterion=crit, defaults={'client_passed': cid in passed_ids})

        fraction = sum((criteria[i].weight for i in passed_ids), Decimal('0')) / Decimal('100')
        if fraction * 100 < Decimal(str(settings.MIN_PAYOUT_SCORE * 100)):
            raise DomainError('Passed criteria are below the minimum payout threshold; request a revision or reject instead.')

        payout = (milestone.escrow_balance * fraction).quantize(CENT, rounding=ROUND_DOWN)
        refund = milestone.escrow_balance - payout
        freelancer_wallet = Wallet.objects.select_for_update().get(user=submission.freelancer)
        client_wallet = Wallet.objects.select_for_update().get(user=client)
        txn = uuid.uuid4()

        if payout > 0:
            freelancer_wallet.balance += payout
            freelancer_wallet.save(update_fields=['balance'])
            LedgerEntry.objects.create(txn_id=txn, kind=LedgerEntry.Kind.PAYOUT, wallet=freelancer_wallet,
                                       milestone=milestone, amount=payout)
            LedgerEntry.objects.create(txn_id=txn, kind=LedgerEntry.Kind.PAYOUT, wallet=None,
                                       milestone=milestone, amount=-payout)
        if refund > 0:
            client_wallet.balance += refund
            client_wallet.save(update_fields=['balance'])
            LedgerEntry.objects.create(txn_id=txn, kind=LedgerEntry.Kind.REFUND, wallet=client_wallet,
                                       milestone=milestone, amount=refund)
            LedgerEntry.objects.create(txn_id=txn, kind=LedgerEntry.Kind.REFUND, wallet=None,
                                       milestone=milestone, amount=-refund)

        milestone.escrow_balance = Decimal('0.00')
        milestone.paid_amount = payout
        milestone.status = Milestone.Status.COMPLETED
        milestone.save(update_fields=['escrow_balance', 'paid_amount', 'status'])

        submission.status = Submission.Status.ACCEPTED
        submission.payout_amount = payout
        submission.payout_fraction = fraction
        submission.decided_at = timezone.now()
        submission.save(update_fields=['status', 'payout_amount', 'payout_fraction', 'decided_at'])

        # Other open submissions for this milestone are closed: the work has been bought.
        Submission.objects.filter(milestone=milestone, status=Submission.Status.SUBMITTED).exclude(pk=submission.pk)\
            .update(status=Submission.Status.REJECTED, decided_at=timezone.now(), payout_fraction=Decimal('0'))

        project = milestone.project
        if not project.milestones.exclude(status=Milestone.Status.COMPLETED).exists():
            project.status = Project.Status.COMPLETED
            project.save(update_fields=['status'])

    recompute_pfi(submission.freelancer)
    return submission


# ---------- milestone planning ----------

def _fallback_plan(budget):
    return [{'title': 'Deliver project', 'description': '', 'share': 1.0,
             'criteria': [{'description': 'Deliverables meet the project description', 'weight': 100}]}]


def generate_milestones(project):
    """Ask the AI planner for milestones+criteria; fall back to one milestone if it is unavailable."""
    budget_for_fallback = Decimal(str(project.budget))
    try:
        resp = requests.post(f'{settings.AI_SERVICE_URL}/generate-milestones', json={
            'description': project.description, 'budget': str(project.budget),
            'deadline': project.deadline.isoformat()}, timeout=settings.AI_SERVICE_TIMEOUT)
        resp.raise_for_status()
        plan = resp.json()['milestones']
        assert plan
    except (requests.RequestException, ValueError, KeyError, AssertionError):
        plan = _fallback_plan(budget_for_fallback)

    shares = [Decimal(str(m['share'])) for m in plan]
    budget = Decimal(str(project.budget))
    with transaction.atomic():
        allocated = Decimal('0.00')
        for i, m in enumerate(plan):
            last = i == len(plan) - 1
            amount = (budget - allocated) if last else (budget * shares[i]).quantize(CENT, rounding=ROUND_DOWN)
            allocated += amount
            ms = Milestone.objects.create(project=project, title=m['title'][:255], description=m.get('description', ''),
                                          payment=amount, order=i)
            weights = [Decimal(str(c['weight'])) for c in m['criteria']]
            drift = Decimal('100') - sum(weights)  # make weights sum to exactly 100
            weights[-1] += drift
            for c, w in zip(m['criteria'], weights):
                AcceptanceCriterion.objects.create(milestone=ms, description=c['description'][:500], weight=w)
