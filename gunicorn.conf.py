"""gunicorn settings. Uploads/commits run in threads, so one worker with many threads keeps
the in-memory index and job table consistent. Scale with ARK_WORKERS only behind sticky routing."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT') or os.environ.get('ARK_PORT') or 8000}"
workers = int(os.environ.get("ARK_WORKERS", "1"))
threads = int(os.environ.get("ARK_THREADS", "16"))
timeout = 0            # large transfers: never kill a worker mid-stream
graceful_timeout = 30
keepalive = 5
accesslog = "-" if os.environ.get("ARK_ACCESS_LOG") else None
