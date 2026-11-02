"""Vercel serverless entrypoint for the SafePay web console.

Vercel's Python runtime looks for a WSGI `app` object in a file under `api/`.
This exposes the Flask app from `webapp/app.py`; `vercel.json` rewrites every
path to this function.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.app import app  # noqa: E402,F401
