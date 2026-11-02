"""WSGI entry point for a production server (gunicorn on Render, etc.).

Run locally with:  gunicorn -w 1 -b 0.0.0.0:8000 wsgi:app
On Render the start command binds to the platform's $PORT (see render.yaml).

One worker (-w 1) is deliberate: the demo's spend ledger lives in process, so a
single worker keeps the daily-cap scenario consistent across a burst of
requests.
"""

from webapp.app import app  # noqa: F401
