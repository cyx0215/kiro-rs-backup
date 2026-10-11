"""Compatibility entry point for the relocated billing monitor."""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "monitoring" / "billing" / "src"
sys.path.insert(0, str(SRC))

from billing_monitor.cli import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    raise SystemExit(main())
