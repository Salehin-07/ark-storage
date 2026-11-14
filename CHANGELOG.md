# Changelog

## 1.0.2
- **New logo** everywhere: favicon, Apple touch icon, installable-app (PWA) icons including a maskable one, and the sign-in
  card. The logo (and the product name) is gone from the top bar, which only needs the search box and its buttons.
- **Search box fills the free width** of the top bar (it used to get half of it, next to an empty spacer). `/` or `Ctrl+K` focuses it.
- **Create, rename, delete, restore and move feel instant.** The change appears on screen at once (new items are shown faded with a
  small spinner while GitHub is being updated), a thin bar and "Saving to GitHub…" show background work, and if GitHub
  refuses, the real state is restored and the error shown. Several actions can be in flight together.
- **Moving to the trash no longer asks.** It is instant and offers **Undo**; "Delete forever…" (or `Shift+Delete`) is the explicit,
  confirmed path. If the trash is switched off in Settings, Delete still asks first.
- **Smooth upload progress.** The bar used to jump from 20% to 66% because it only moved when a whole 16 MB chunk finished. It now
  follows the bytes actually sent, eases between updates, never goes backwards, creeps gently while GitHub finishes, and
  only reaches 100% when done. Speed is a 4-second moving average. The panel no longer rebuilds on every update.
- **Faster everywhere.** Folders you have opened open instantly (cached, then revalidated in the background; no redraw if nothing
  changed) and hovering or touching a folder warms its listing; the session and the first listing load in parallel;
  static files are versioned and cached for a year, gzip-compressed (once, then from memory) along with large JSON
  responses; long folders skip layout and painting for off-screen rows; the "Loading…" flash is gone for fast loads.
- The trash view opens at once with what it knew last time and refreshes behind it; trash actions no longer clutter the Transfers panel.

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
