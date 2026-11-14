# Architecture

```
browser ── Flask JSON API ── Store ──┬── GitHub REST API   listing (recursive tree + ETag), raw blob download
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
