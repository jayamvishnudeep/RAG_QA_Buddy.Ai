"""Vercel entry point: the QABuddy FastAPI app, served as one Python function."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qabuddy.api import app  # noqa: E402,F401
