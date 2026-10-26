"""Repository verification.

Phase 1: intentionally NOT faked. It reports `available: false`, so Django falls back to manual
client review instead of auto-approving on a random score. Phase 2 replaces this with a real
GitHub-API + static-analysis + LLM pipeline returning per-criterion results in the same shape:
  {"available": true, "score": 0-100, "summary": str,
   "results": [{"criterion_id": int, "passed": bool, "note": str}]}
"""
from urllib.parse import urlparse


def verify_submission(github_link, criteria=None):
    host = urlparse(github_link or '').netloc.lower()
    if host not in ('github.com', 'www.github.com'):
        return {'available': False, 'summary': 'Not a valid GitHub repository link.'}
    return {'available': False,
            'summary': 'Automated repository analysis is not enabled yet; please review manually.'}
