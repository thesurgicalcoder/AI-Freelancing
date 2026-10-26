# TrustLancer AI

A freelancing platform where **clients choose who may submit**, **AI helps rank the submissions**, and
**payment is proportional to the work accepted**, held in escrow until the client decides.

> Status: Phase 1 (foundation). The payment engine, permissions and workflow are real and tested.
> The AI repo analysis is **not implemented yet** (Phase 2) and is reported honestly as "unavailable" rather than faked.

## Workflow

1. **Client** posts a project. The planner splits it into milestones; each milestone has weighted **acceptance criteria** (weights sum to 100).
2. **Freelancers** apply. The client approves the ones allowed to submit (applicants are shown ranked by PFI).
3. Client **funds a milestone**: money moves from their wallet into **escrow** (ledger-recorded).
4. Approved freelancers submit a GitHub repo. The AI review (Phase 2) scores each criterion; submissions are **ranked** for the client.
5. The client decides: **accept** (marks which criteria passed), **request revision**, or **reject**.
   Payout = escrow x (sum of passed criteria weights / 100). The unmet share is refunded. A minimum threshold (30%) applies.

## Money safety

- Wallet changes happen only in `core_api/services.py`, inside `transaction.atomic()` with row locks.
- Append-only `LedgerEntry` log; internal transfers net to zero; tests assert money conservation (wallets + escrow = deposits).
- A decided submission or paid milestone cannot be paid twice; only one submission can win a milestone.
- DB constraint: wallet balance can never go negative.

## Run it

```bash
cp .env.example .env            # set DJANGO_SECRET_KEY, keep DJANGO_DEBUG=true for local dev
pip install -r requirements-dev.txt
python manage.py migrate && python seed_db.py
python manage.py runserver                               # API + pages on :8000
(cd ai_services/flask_app && pip install -r ../requirements.txt && python app.py)   # planner on :5001
pytest
```

Or `docker compose up --build` (Postgres + web + AI service).

## API (JWT: `Authorization: Bearer <access>`)

| Method & path | Who | Purpose |
| --- | --- | --- |
| `POST /api/auth/register/`, `/login/`, `/refresh/` | anyone | auth |
| `GET/POST /api/projects/` | client creates; freelancers browse open projects | projects + AI milestones |
| `POST /api/projects/{id}/apply/` | freelancer | apply |
| `GET /api/projects/{id}/applications/` | owner | applicants ranked by PFI |
| `POST /api/applications/{id}/decide/` | owner | `approve` / `reject` |
| `GET /api/wallet/`, `POST /api/wallet/deposit/` | any | balance; test-mode top-up (dev only) |
| `POST /api/milestones/{id}/fund/` | owner | move payment into escrow |
| `POST /api/milestones/{id}/submit/` | approved freelancer | submit repo (needs funded milestone) |
| `GET /api/milestones/{id}/submissions/` | owner | submissions, AI-ranked |
| `POST /api/submissions/{id}/decide/` | owner | accept (with `passed_criteria`) / request_revision / reject |

## Roadmap

- **Phase 2:** real analysis (GitHub API, static analysis, LLM per-criterion scoring), Celery + Redis, plagiarism check.
- **Phase 3:** Stripe Connect (test mode) replacing the test deposit; disputes; auto-release timeout.
- **Phase 4:** React/TypeScript frontend (the current Django templates are legacy and not yet wired to the new workflow).
- **Phase 5:** deploy, monitoring (Sentry), load tests, demo.
