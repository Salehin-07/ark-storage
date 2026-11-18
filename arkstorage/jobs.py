"""Background jobs (uploads to GitHub, zip, extract, convert...) with progress for the UI.

Jobs are asyncio tasks. ``fn`` is an ``async def fn(job)``; blocking steps inside it go through
``aio.run`` so the event loop never stalls.
"""
import asyncio
import secrets
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
        # plain attribute writes: safe to call from worker threads too
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
        self._tasks = set()

    def start(self, kind, title, fn):
        job = Job(kind, title)
        self._prune()
        self._jobs[job.id] = job

        async def run():
            try:
                job.result = await fn(job)
                job.state = "done"
                job.progress = 1.0
            except asyncio.CancelledError:
                job.state, job.error = "error", "Cancelled (the server is shutting down)"
                raise
            except Exception as e:          # noqa: BLE001 - reported to the UI
                job.state = "error"
                job.error = str(e) or e.__class__.__name__
            finally:
                job.finished = time.time()

        task = asyncio.get_running_loop().create_task(run(), name=f"job-{job.id}")
        self._tasks.add(task)                 # keep a strong reference until it ends
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, jid):
        return self._jobs.get(jid)

    def list(self):
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda j: -j.created)]

    def _prune(self):
        now = time.time()
        for k in [k for k, j in self._jobs.items() if j.finished and now - j.finished > 3600]:
            del self._jobs[k]

    async def shutdown(self):
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
