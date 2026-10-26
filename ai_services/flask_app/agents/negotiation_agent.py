import re

KEYWORDS = [r'\bai\b', r'machine learning', r'blockchain', r'\breact\b', r'full\s?stack']


def check_negotiation(description, budget):
    """Rough budget sanity check (placeholder heuristic)."""
    estimated_min = 200 + 150 * sum(1 for k in KEYWORDS if re.search(k, description or '', re.I))
    if float(budget) < estimated_min:
        return {'status': 'Unrealistic', 'message': 'Budget looks low for the described scope.',
                'recommended_range': f'${estimated_min} - ${estimated_min + 300}'}
    return {'status': 'Fair', 'message': 'The budget looks reasonable for this scope.'}
