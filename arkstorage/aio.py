"""Small async helpers: run blocking work (git, hashing, disk walks) off the event loop."""
import functools

import anyio
import anyio.to_thread


async def run(fn, *args, **kwargs):
    """Run a blocking callable in a worker thread and wait for it.

    The thread is never abandoned when the caller is cancelled (a client hanging up must not
    leave a git push half-finished and unobserved), so cleanup code can rely on it having ended.
    """
    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs), abandon_on_cancel=False)


def set_threads(n):
    """Size of the worker-thread pool (ARK_THREADS)."""
    anyio.to_thread.current_default_thread_limiter().total_tokens = max(4, int(n))
