# Architecture

```
browser ── FastAPI (async) JSON API ── Store ──┬── GitHub REST API   listing (recursive tree + ETag), raw blob download
 (vanilla JS)   (app.py)    (store.py)└── git CLI           build tree → commit → push (one push per batch)
                                  └── disk cache (LRU)     downloaded pieces
```

* **vfs.py** maps a git tree to a virtual file system (naming rules: `.arkparts`, `.arkenc`, `.arkkeep`).
* **store.py** turns *intents* (put, mkdir, move, copy, delete, restore, ...) into git index operations, under a write lock, with
  retry on a moved branch. Moves/copies/renames reuse blob ids. Folders that become empty get a `.arkkeep` marker.
* **crypto.py** AES-256-GCM in 1 MiB segments (key = HKDF(scrypt(passphrase), per-file salt), segment index as nonce,
  last-segment flag in the AAD), so ranges can be decrypted without reading the whole file and truncation is detected.
* **Uploads**: browser → 8 MB chunks → server temp file → staged (encrypted + cut into pieces + hashed) → one commit/push → temp deleted.
  Disk needed ≈ 2× the file during upload.
* **Reads**: pieces are downloaded on demand (whole blob, ≤ 100 MB), cached, then served with HTTP Range support.
* The storage branch is created as an orphan branch, so your repository's code branches are untouched.


## Concurrency model (v2.0)

One uvicorn process runs one event loop (uvloop). Everything that waits is a coroutine: HTTP, the GitHub client (httpx
connection pool), piece downloads, upload/download streaming, jobs. Blocking leaf work (the `git` subprocesses, hashing,
AES, deflate, walking big trees, scrypt) goes to a worker-thread pool via `aio.run` (size: `ARK_THREADS`, default 32), so
it can never stall other requests.

* A piece download is a shared task: concurrent readers of the same piece join it, and it survives a client hanging up.
* The listing is served stale-while-revalidate: within 3x `refresh_seconds` the old listing is returned at once and a
  background task revalidates it with a conditional (ETag) request. Rendered listings are cached per repository state.
* Uploads stream socket -> temp file in 1 MiB steps, then are hashed into git by a worker thread; back-pressure comes from the socket.
* Writes are still one commit at a time (the write lock) - that is git's model, not a limit of the server.
* Keep `ARK_WORKERS=1` unless you route sticky: listing, upload and job tables live in memory.
