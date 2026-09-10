"""
Example plugin: a URL transformer that rewrites ``t.cn`` short links
to their canonical form.  Registered via entry_points.
"""

from __future__ import annotations

import re
from typing import Optional

_TCN_RE = re.compile(r"https?://t\.cn/\w+")


def transform(url: str) -> Optional[str]:
    """Pass-through stub: real implementation would HEAD the URL."""
    if not _TCN_RE.match(url):
        return None
    return url  # placeholder; real plugins would resolve the redirect
