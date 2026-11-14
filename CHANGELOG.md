# Changelog

## 1.0.1
- **Uploads stream to GitHub.** Pieces are hashed as the bytes arrive and pushed in rolling batches, so the server no longer
  needs ~2x a file's size in disk or about 1 GB of RAM for a 500 MB file: disk stays at a few hundred MB and RAM under ~250 MB
  at any file size (new setting *Push batch*, `ARK_STAGE_MB`). Git is now run in a low-memory mode (the 500 MB HTTP post buffer
  is gone). An 800 MB upload to GitHub went from 168 s to 67 s.
- **Faster transfers.** Uploads send 4 chunks in parallel even for a single file (16 MB chunks, out-of-order safe, retry safe)
  and show live speed; downloads prefetch the next pieces of a large file.
- **First-run setup screen.** Deploy with no repository/token: the first sign-in (`ark` / `ark`) opens a guided setup that asks for
  them, tests the connection, and offers to change the default password and enable encryption.
- **Fixed** the word "null" appearing on the selection bar (once per hidden button) when items were selected.
- **Delete / trash.** Deleting and restoring update the list instantly while GitHub is updated in the background; the trash now
  supports multi-select with Restore selected / Delete selected.
- Published as a Docker image: `ghcr.io/salehin-07/ark-storage` (amd64 + arm64). `main` is the default branch.

## 1.0.0
First release: GitHub-backed file browser with chunked uploads (files and folders), splitting of files over 100 MB, optional
AES-256-GCM encryption (with convert-all), trash and restore, search, multi-select with rubber band and long-press, drag & drop,
image/video/audio/PDF preview, code editor, zip download/compress/extract, share links, login, and an advanced Settings dialog.
