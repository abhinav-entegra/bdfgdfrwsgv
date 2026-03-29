"""
Minimal Flask app for Vercel only.

The real Nexus iQ stack (Socket.IO, SQLite, Gunicorn) runs via unified_app.py
on Railway / Render / Fly.io — see root Procfile.
"""
from flask import Flask, Response

app = Flask(__name__)

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Nexus iQ — deploy on Railway</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 42rem; margin: 3rem auto; padding: 0 1.25rem; line-height: 1.5; color: #1a1528; }
    code { background: #f4f0ff; padding: 0.15rem 0.35rem; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>Nexus iQ</h1>
  <p>This GitHub repo is built for a <strong>long-running Python server</strong> (WebSockets, SQLite, <code>gunicorn</code>). That does <strong>not</strong> map to Vercel serverless functions.</p>
  <p>Deploy the production app on <strong>Railway</strong> from this repo using the root <code>Procfile</code> (<code>unified_app:app</code>), then point users (and your custom domain) to the Railway URL.</p>
  <p>This Vercel deployment only shows this notice so the project does not crash with <code>FUNCTION_INVOCATION_FAILED</code>.</p>
</body>
</html>
"""


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def _notice(path: str):
    return Response(_HTML, mimetype="text/html; charset=utf-8")
