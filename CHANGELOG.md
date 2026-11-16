# Changelog

## 1.0.4
- **Word, Excel and PowerPoint files open in the file browser** instead of downloading. `.docx`/`.docm` are drawn like pages, `.xlsx`/`.xlsm`/`.xlsb`/`.xls`/`.ods`
  as sheets (tabs, merged cells, sticky headers, more rows load as you scroll), `.pptx`/`.ppsx` as slides. Everything is rendered in the browser
  by bundled libraries (no CDN), downloaded only the first time you open such a file. Documents are shown in a sandboxed frame without scripts.
- **Old formats** (`.doc .dot .ppt .pps .odt .odp .rtf`) are converted once to PDF on the server and shown in the PDF viewer, when LibreOffice is
  installed (Docker: `--build-arg WITH_OFFICE=1`). Without it the viewer offers a download. Encrypted files are converted in memory, never cached readable.
- **Faster file loading.** Hovering or touching a previewable file starts fetching its first pieces right away; the disk cache no longer re-scans every
  file after each download; GitHub connections are reused (bigger pool); more pieces are prefetched ahead of a read.
- Office files are no longer offered for editing as text.

## 1.0.3
- **An upload that stops halfway can no longer leave a half file.** Pieces are written first and the file only becomes visible
  (its piece list is written into the tree, in the same commit) once every piece is present with the right size and the pieces
  add up to the size the client announced. Otherwise nothing changes: readers keep getting the previous complete file, and the
  commit fails with a clear message. A split file with a missing piece is never listed or served.
- **Cleanup job for pieces that never made it into a file.** Early-pushed pieces of an abandoned upload (crash, closed tab,
  failed commit) are removed in the background every 30 minutes, and by *Settings -> Maintenance -> Clean up unfinished uploads*.
  A batch is only removed after it has been seen idle and unchanged twice, so an upload that is still arriving is never touched.
- **PDFs open again.** The browser's own PDF viewer was refused inside the frame ("This content is blocked") and does not exist at
  all on Android, so PDFs are now drawn by a bundled PDF.js reader (no CDN): fit-to-width, zoom, page counter.
- **Big files never show a blank screen.** Every viewer shows a spinner at once; images show a progress bar while they download;
  PDFs show how much has been fetched. PDFs are fetched by byte range and drawn lazily: the first page appears as soon as its bytes
  arrive, the page you are looking at is drawn first (then the next one), and pages far away give their memory back.

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
