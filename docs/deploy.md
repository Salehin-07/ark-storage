# Deploying

**Anywhere with Docker**: `docker compose up -d` (volume `ark-data` holds settings and cache).
**Render**: New → Blueprint → this repo (`render.yaml`). Set `ARK_REPO_URL` and `ARK_GITHUB_TOKEN`. `ARK_SECRET_KEY` is generated.
On the free plan the disk is ephemeral: settings saved in the UI are lost on redeploy, so use environment variables for anything
you want to keep (`ARK_ENCRYPT`, `ARK_PASSPHRASE`, `ARK_PASSWORD`, ...). The free plan also sleeps when idle.
**Plain Linux**: `pip install -r requirements.txt` and run `gunicorn -c gunicorn.conf.py wsgi:app` (needs `git`). Put it behind HTTPS.
**Windows / dev**: `python wsgi.py` (gunicorn is not available on Windows).

Run a single worker (the default): the listing index and job table live in memory.
