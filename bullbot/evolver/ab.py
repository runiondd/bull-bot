"""A/B model selection for the evolver proposer.

Ship state: when ``PROPOSER_MODEL_AB_ENABLED`` is True, each ticker is
assigned to either ``PROPOSER_MODEL_A`` (control) or ``PROPOSER_MODEL_B``
(challenger) by a stable hash of the ticker symbol — same ticker always
lands on the same arm, so within-ticker history is comparable.

When A/B is disabled we just return ``config.PROPOSER_MODEL``.
"""
from __future__ import annotations

import hashlib

from bullbot import config


def pick_proposer_model(ticker: str) -> str:
    """Return the model ID to use for ``ticker``'s next proposer call."""
    if not config.PROPOSER_MODEL_AB_ENABLED:
        return config.PROPOSER_MODEL

    # md5 of the ticker is stable across processes and Python versions; we
    # only need a uniform 1-bit signal, so the lowest bit of the digest is fine.
    digest = hashlib.md5(ticker.encode("utf-8")).digest()
    return config.PROPOSER_MODEL_A if (digest[0] & 1) == 0 else config.PROPOSER_MODEL_B
