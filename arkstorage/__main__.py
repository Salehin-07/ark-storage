"""``python -m arkstorage``: run the server with uvicorn (uvloop + httptools when installed).

Why uvicorn: it is the reference ASGI server for FastAPI, its uvloop/httptools pair is the fastest pure-Python-facing
stack, and it streams request and response bodies with real back-pressure, which is what a file store needs.
One worker by default: the listing, upload table and job table live in memory (scale with ARK_WORKERS only behind
sticky routing, as before). Threads are not needed for concurrency any more; ARK_THREADS only sizes the pool used
for blocking git/hash/crypto work.
"""
import os


def main():
    import uvicorn

    workers = int(os.environ.get("ARK_WORKERS") or 1)
    uvicorn.run(
        "arkstorage.asgi:app",
        host=os.environ.get("ARK_HOST", "0.0.0.0"),
        port=int(os.environ.get("ARK_PORT") or os.environ.get("PORT") or 8000),
        workers=workers,
        ws="none",
        lifespan="on",
        log_level=os.environ.get("ARK_LOG_LEVEL", "info"),
        access_log=bool(os.environ.get("ARK_ACCESS_LOG")),
        server_header=False,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("ARK_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        timeout_keep_alive=int(os.environ.get("ARK_KEEPALIVE") or 65),
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
