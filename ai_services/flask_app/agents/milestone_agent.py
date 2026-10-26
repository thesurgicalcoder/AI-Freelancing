"""Milestone planner (rule-based placeholder; replaced by an LLM with a JSON schema in Phase 2).

Contract: returns milestones with a `share` of the budget (shares sum to 1.0) and acceptance
criteria whose weights sum to 100. Django turns shares into exact money amounts.
"""
import re

TEMPLATES = {
    'web3': [
        ('Smart contract architecture', 0.20, ['Contract design documented', 'Core contracts implemented', 'Unit tests for contracts']),
        ('Liquidity logic & audit', 0.35, ['Liquidity logic implemented', 'Static analysis / audit report', 'Edge-case tests']),
        ('Web3 integration', 0.25, ['Wallet/provider integration', 'Transaction flows working', 'Error handling']),
        ('Staging & gas optimisation', 0.20, ['Deployed to testnet', 'Gas report', 'Deployment docs']),
    ],
    'ml': [
        ('Data pipeline & cleaning', 0.25, ['Data ingestion implemented', 'Cleaning/validation steps', 'Reproducible pipeline']),
        ('Model design & training', 0.40, ['Baseline model trained', 'Evaluation metrics reported', 'Experiment tracking']),
        ('Inference API', 0.20, ['Inference endpoint', 'Input validation', 'API tests']),
        ('Optimisation & load test', 0.15, ['Latency benchmark', 'Load test results', 'Documentation']),
    ],
    'default': [
        ('Schema & infrastructure', 0.15, ['Database schema/migrations', 'Project scaffolding & config', 'README with setup steps']),
        ('Core API & business logic', 0.45, ['Core endpoints implemented', 'Validation & error handling', 'Automated tests']),
        ('Frontend implementation', 0.25, ['Main user flows built', 'Responsive layout', 'Connected to the API']),
        ('Integration & QA', 0.15, ['End-to-end flow works', 'Bug fixes from review', 'Deployment instructions']),
    ],
}

WEB3 = re.compile(r'\b(defi|blockchain|smart contract|web3|solidity)\b', re.I)
ML = re.compile(r'\b(ai|ml|machine learning|deep learning|llm|trading model|neural)\b', re.I)


def _weights(n):
    base = round(100 / n, 2)
    ws = [base] * n
    ws[-1] = round(100 - base * (n - 1), 2)
    return ws


def generate_milestones(description, budget, deadline):
    description = description or ''
    key = 'web3' if WEB3.search(description) else 'ml' if ML.search(description) else 'default'
    milestones = []
    for i, (title, share, criteria) in enumerate(TEMPLATES[key]):
        ws = _weights(len(criteria))
        milestones.append({
            'step': i + 1,
            'title': title,
            'description': f'{title} for: {description[:120]}',
            'share': share,
            'criteria': [{'description': c, 'weight': w} for c, w in zip(criteria, ws)],
        })
    return {'template': key, 'milestones': milestones}
