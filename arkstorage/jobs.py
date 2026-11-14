"""Background jobs (uploads to GitHub, zip, extract, convert...) with progress for the UI."""
import secrets
import threading
import time


class Job:
    def __init__(self, kind, title):
        self.id = secrets.token_hex(6)
        self.kind, self.title = kind, title
        self.state = "running"
        self.progress = 0.0
        self.message = ""
        self.error = ""
        self.result = None
        self.created = time.time()
        self.finished = None

    def update(self, progress=None, message=None):
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                "progress": round(self.progress, 3), "message": self.message, "error": self.error,
                "result": self.result, "created": self.created, "finished": self.finished}


class Jobs:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, kind, title, fn):
        job = Job(kind, title)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job

        def run():
            try:
                job.result = fn(job)
                job.state = "done"
                job.progress = 1.0
            except Exception as e:          # noqa: BLE001 - reported to the UI
                job.state = "error"
                job.error = str(e) or e.__class__.__name__
            finally:
                job.finished = time.time()

        threading.Thread(target=run, daemon=True, name=f"job-{job.id}").start()
        return job

    def get(self, jid):
        return self._jobs.get(jid)

    def list(self):
        with self._lock:
            return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda j: -j.created)]

    def _prune(self):
        now = time.time()
        for k in [k for k, j in self._jobs.items() if j.finished and now - j.finished > 3600]:
            del self._jobs[k]
