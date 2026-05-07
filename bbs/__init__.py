"""Fork-only: shared BBS HTTP client used by /bbs slash command and worker.

Layered on top of assets/agent_bbs.py. Provides:
  - Config loading from bbs/settings.json (env override via GA_BBS_*)
  - Persistent identity cache (token by board+name)
  - Retry with exponential backoff for transient errors
  - Result tuples (ok, data, error) — never raises into caller
"""

from .client import BBSClient, BBSResult, load_settings  # noqa: F401
