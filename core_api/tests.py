from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
import requests
from django.db.models import Sum
from django.utils import timezone
from rest_framework.test import APIClient

from core_api import services
from core_api.models import LedgerEntry, Milestone, Project, ProjectApplication, Submission, User, Wallet

PLAN = {'milestones': [
    {'title': 'M1', 'description': 'd', 'share': 0.6, 'criteria': [
        {'description': 'API', 'weight': 40}, {'description': 'Tests', 'weight': 30}, {'description': 'Docs', 'weight': 30}]},
    {'title': 'M2', 'description': 'd', 'share': 0.4, 'criteria': [{'description': 'All', 'weight': 100}]},
]}


def ai_ok(*args, **kwargs):
    r = mock.Mock()
    r.raise_for_status = lambda: None
    r.json = lambda: PLAN if 'generate-milestones' in args[0] else {'available': False, 'summary': 'manual'}
    return r


@pytest.fixture(autouse=True)
def _ai(monkeypatch):
    monkeypatch.setattr('core_api.services.requests.post', ai_ok)


def make_user(name, role):
    return User.objects.create_user(username=name, password='S3cure-pass-91', role=role)


def api(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


@pytest.fixture
def world(db):
    client_u, f1, f2 = make_user('cli', 'Client'), make_user('f1', 'Freelancer'), make_user('f2', 'Freelancer')
    services.deposit(client_u, '1000')
    r = api(client_u).post('/api/projects/', {'title': 'P', 'description': 'build an app', 'budget': '1000.00',
                                              'deadline': (timezone.now() + timedelta(days=30)).isoformat()}, format='json')
    assert r.status_code == 201, r.content
    project = Project.objects.get(pk=r.data['id'])
    return dict(client=client_u, f1=f1, f2=f2, project=project, m1=project.milestones.first(), m2=project.milestones.last())


def approve(w, freelancer):
    app = api(freelancer).post(f"/api/projects/{w['project'].id}/apply/", {}, format='json')
    assert app.status_code == 201
    r = api(w['client']).post(f"/api/applications/{app.data['id']}/decide/", {'action': 'approve'}, format='json')
    assert r.status_code == 200


def ledger_balanced():
    """Internal transfers net to zero, and money is conserved: wallets + escrow == total deposits."""
    internal = LedgerEntry.objects.exclude(kind=LedgerEntry.Kind.DEPOSIT)
    for txn in internal.values_list('txn_id', flat=True).distinct():
        assert internal.filter(txn_id=txn).aggregate(s=Sum('amount'))['s'] == 0
    deposits = LedgerEntry.objects.filter(kind=LedgerEntry.Kind.DEPOSIT).aggregate(s=Sum('amount'))['s'] or 0
    wallets = Wallet.objects.aggregate(s=Sum('balance'))['s'] or 0
    escrow = Milestone.objects.aggregate(s=Sum('escrow_balance'))['s'] or 0
    assert wallets + escrow == deposits


# ---------- planning ----------

def test_milestones_sum_exactly_to_budget_and_weights_to_100(world):
    assert sum(m.payment for m in world['project'].milestones.all()) == Decimal('1000.00')
    for m in world['project'].milestones.all():
        assert sum(c.weight for c in m.criteria.all()) == Decimal('100')


def test_ai_planner_down_falls_back_to_single_milestone(db, monkeypatch):
    def boom(*a, **k): raise requests.ConnectionError()
    monkeypatch.setattr('core_api.services.requests.post', boom)
    c = make_user('c2', 'Client')
    r = api(c).post('/api/projects/', {'title': 'P', 'description': 'x', 'budget': '500',
                                       'deadline': (timezone.now() + timedelta(days=5)).isoformat()}, format='json')
    assert r.status_code == 201 and len(r.data['milestones']) == 1
    assert Decimal(r.data['milestones'][0]['payment']) == Decimal('500.00')


# ---------- permissions ----------

def test_freelancer_cannot_create_project(world):
    r = api(world['f1']).post('/api/projects/', {'title': 'x', 'description': 'x', 'budget': '10',
                                                  'deadline': (timezone.now() + timedelta(days=1)).isoformat()}, format='json')
    assert r.status_code == 403


def test_unapproved_freelancer_cannot_submit(world):
    services.fund_milestone(world['m1'].id, world['client'])
    r = api(world['f1']).post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': 'https://github.com/a/b'}, format='json')
    assert r.status_code == 403


def test_other_client_cannot_fund_or_decide(world):
    other = make_user('evil', 'Client')
    services.deposit(other, '5000')
    assert api(other).post(f"/api/milestones/{world['m1'].id}/fund/").status_code == 403
    assert api(other).get(f"/api/milestones/{world['m1'].id}/submissions/").status_code == 404


def test_registration_cannot_set_pfi_or_weak_password(db):
    r = api().post('/api/auth/register/', {'username': 'x', 'password': 'S3cure-pass-91', 'role': 'Freelancer', 'pfi_score': 100}, format='json')
    assert r.status_code == 201 and User.objects.get(username='x').pfi_score == 0
    assert api().post('/api/auth/register/', {'username': 'y', 'password': '123'}, format='json').status_code == 400


def test_pages_api_requires_auth(db):
    assert api().get('/api/projects/').status_code == 401


# ---------- escrow & payout ----------

def test_fund_moves_money_to_escrow_and_cannot_be_repeated(world):
    ms = services.fund_milestone(world['m1'].id, world['client'])
    assert ms.escrow_balance == Decimal('600.00')
    assert Wallet.objects.get(user=world['client']).balance == Decimal('400.00')
    with pytest.raises(services.DomainError):
        services.fund_milestone(world['m1'].id, world['client'])
    ledger_balanced()


def test_cannot_fund_without_enough_balance(world):
    Wallet.objects.filter(user=world['client']).update(balance=Decimal('10'))
    with pytest.raises(services.DomainError) as e:
        services.fund_milestone(world['m1'].id, world['client'])
    assert e.value.status == 402


def test_cannot_submit_until_funded(world):
    approve(world, world['f1'])
    r = api(world['f1']).post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': 'https://github.com/a/b'}, format='json')
    assert r.status_code == 409


def test_full_flow_partial_payout_proportional_to_passed_criteria(world):
    approve(world, world['f1'])
    services.fund_milestone(world['m1'].id, world['client'])
    r = api(world['f1']).post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': 'https://github.com/a/b'}, format='json')
    assert r.status_code == 201 and r.data['ai_available'] is False  # honest: no fake AI score
    crit = list(world['m1'].criteria.all())            # weights 40/30/30 on a $600 escrow
    d = api(world['client']).post(f"/api/submissions/{r.data['id']}/decide/",
                                  {'action': 'accept', 'passed_criteria': [crit[0].id, crit[1].id]}, format='json')
    assert d.status_code == 200, d.content
    assert Decimal(d.data['payout_amount']) == Decimal('420.00')          # 70% of 600
    assert Wallet.objects.get(user=world['f1']).balance == Decimal('420.00')
    assert Wallet.objects.get(user=world['client']).balance == Decimal('400.00') + Decimal('180.00')  # refund of unmet 30%
    m = Milestone.objects.get(pk=world['m1'].id)
    assert m.status == 'Completed' and m.escrow_balance == 0
    assert User.objects.get(pk=world['f1'].id).pfi_score == pytest.approx(70.0)
    ledger_balanced()


def test_below_minimum_threshold_is_refused(world):
    approve(world, world['f1'])
    services.fund_milestone(world['m1'].id, world['client'])
    sub = Submission.objects.create(milestone=world['m1'], freelancer=world['f1'], github_link='https://github.com/a/b')
    world['m1'].criteria.filter(description='Docs').update(weight=Decimal('20'))
    world['m1'].criteria.filter(description='API').update(weight=Decimal('50'))
    docs = world['m1'].criteria.get(description='Docs')
    with pytest.raises(services.DomainError) as e:
        services.decide_submission(sub.id, world['client'], 'accept', [docs.id])  # 20% < 30% minimum
    assert 'minimum' in e.value.message
    assert Wallet.objects.get(user=world['f1']).balance == 0
    assert Milestone.objects.get(pk=world['m1'].id).escrow_balance == Decimal('600.00')


def test_decision_cannot_be_applied_twice_no_double_payment(world):
    approve(world, world['f1'])
    services.fund_milestone(world['m1'].id, world['client'])
    sub = Submission.objects.create(milestone=world['m1'], freelancer=world['f1'], github_link='https://github.com/a/b')
    ids = [c.id for c in world['m1'].criteria.all()]
    services.decide_submission(sub.id, world['client'], 'accept', ids)
    with pytest.raises(services.DomainError) as e:
        services.decide_submission(sub.id, world['client'], 'accept', ids)
    assert e.value.status == 409
    assert Wallet.objects.get(user=world['f1']).balance == Decimal('600.00')


def test_only_one_submission_can_win_a_milestone(world):
    for f in (world['f1'], world['f2']):
        approve(world, f)
    services.fund_milestone(world['m1'].id, world['client'])
    s1 = Submission.objects.create(milestone=world['m1'], freelancer=world['f1'], github_link='https://github.com/a/b')
    s2 = Submission.objects.create(milestone=world['m1'], freelancer=world['f2'], github_link='https://github.com/c/d')
    ids = [c.id for c in world['m1'].criteria.all()]
    services.decide_submission(s1.id, world['client'], 'accept', ids)
    s2.refresh_from_db()
    assert s2.status == 'Rejected'
    with pytest.raises(services.DomainError):
        services.decide_submission(s2.id, world['client'], 'accept', ids)


def test_revision_flow_allows_resubmission(world):
    approve(world, world['f1'])
    services.fund_milestone(world['m1'].id, world['client'])
    c = api(world['f1'])
    sid = c.post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': 'https://github.com/a/b'}, format='json').data['id']
    assert c.post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': 'https://github.com/a/b2'}, format='json').status_code == 409
    api(world['client']).post(f'/api/submissions/{sid}/decide/', {'action': 'request_revision', 'feedback': 'add tests'}, format='json')
    r = c.post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': 'https://github.com/a/b2'}, format='json')
    assert r.status_code == 201 and Submission.objects.filter(milestone=world['m1']).count() == 1


def test_invalid_github_link_rejected(world):
    approve(world, world['f1'])
    services.fund_milestone(world['m1'].id, world['client'])
    for bad in ('http://github.com/a/b', 'https://evil.com/a/b', 'https://github.com/onlyowner'):
        r = api(world['f1']).post(f"/api/milestones/{world['m1'].id}/submit/", {'github_link': bad}, format='json')
        assert r.status_code == 400, bad


def test_project_completes_when_all_milestones_paid(world):
    approve(world, world['f1'])
    for m in (world['m1'], world['m2']):
        services.fund_milestone(m.id, world['client'])
        sub = Submission.objects.create(milestone=m, freelancer=world['f1'], github_link='https://github.com/a/b')
        services.decide_submission(sub.id, world['client'], 'accept', [c.id for c in m.criteria.all()])
    assert Project.objects.get(pk=world['project'].id).status == 'Completed'
    assert Wallet.objects.get(user=world['f1']).balance == Decimal('1000.00')
    ledger_balanced()


def test_submissions_ranked_by_ai_score_unscored_last(world):
    for f in (world['f1'], world['f2']):
        approve(world, f)
    services.fund_milestone(world['m1'].id, world['client'])
    Submission.objects.create(milestone=world['m1'], freelancer=world['f1'], github_link='https://github.com/a/b')
    Submission.objects.create(milestone=world['m1'], freelancer=world['f2'], github_link='https://github.com/c/d', ai_score=82, ai_available=True)
    r = api(world['client']).get(f"/api/milestones/{world['m1'].id}/submissions/")
    assert [s['freelancer_name'] for s in r.data] == ['f2', 'f1']


def test_project_visibility(world):
    other_client = make_user('c3', 'Client')
    assert api(other_client).get('/api/projects/').data == []              # clients only see their own
    assert len(api(world['f1']).get('/api/projects/').data) == 1           # open projects are the marketplace
    services.fund_milestone(world['m1'].id, world['client'])               # -> In Progress, closed to strangers
    assert api(world['f1']).get('/api/projects/').data == []
    approve(world, world['f1'])
    assert len(api(world['f1']).get('/api/projects/').data) == 1           # visible again once approved
