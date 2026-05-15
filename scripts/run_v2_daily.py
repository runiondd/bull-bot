"""Thin CLI wrapper for the v2 daily run — for launchd / cron invocation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bullbot import cli


if __name__ == "__main__":
    raise SystemExit(cli.main(["run-v2-daily"]))
