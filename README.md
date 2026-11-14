<p align="center"><img src="docs/logo.png" alt="Ark Storage" width="180"></p>

# Ark Storage

**Your own cloud drive, stored in a GitHub repository.** Upload, browse, edit, preview and share files from any
browser, while the actual data lives in a repo you control, private or public, plain or encrypted.

- **Full file browser**: folders, list/grid view, sort, search, multi-select (click, Ctrl/Shift, rubber band, long-press on
  phones), drag & drop (also onto folders), copy/cut/paste, rename, move, delete with **trash and restore**, properties,
  keyboard shortcuts, breadcrumbs, responsive layout for phones.
- **Upload files or whole folders**: chunked uploads with progress, conflict choices (skip / keep both / replace), folder drag & drop.
- **Preview**: images, video and audio (with seeking), PDF, plus a **code editor** (auto-indent, auto-closing brackets and quotes,
  comment toggle, find & replace, adjustable font size, unsaved-changes guard). Binary and compressed files are never opened as text.
- **Files over 100 MB**: GitHub caps a file at 100 MB, so bigger files are split into pieces (default 48 MB) and joined on the fly.
  Ranged reads work across pieces, so a 4 GB video still seeks.
- **Encryption on demand**: AES-256-GCM, applied on the server before anything reaches GitHub. Switch it on for new files, or
  encrypt / decrypt everything later from Settings.
- **Download as zip** (folders or selections, streamed), **compress** and **extract** zips, **expiring share links**.
- **Advanced Settings**: repository, branch, encryption, piece size, cache size, upload limit, trash, theme, account,
  maintenance (clear cache, compact history), all changeable in the UI.
- **Login** (default `ark` / `ark`, change it in Settings).

## Quick start

You need a GitHub repository and a [fine-grained token](https://github.com/settings/personal-access-tokens/new) with
**Contents: Read and write** on that repository. **Make the repository private** unless you encrypt, or anyone can read your files.

```bash
git clone https://github.com/Salehin-07/ark-storage && cd ark-storage
pip install -r requirements.txt          # needs git on PATH
export ARK_REPO_URL=https://github.com/you/your-storage
export ARK_GITHUB_TOKEN=github_pat_xxx
gunicorn -c gunicorn.conf.py wsgi:app    # or: python wsgi.py
```

Open <http://localhost:8000>, sign in with `ark` / `ark` and change the password under **Settings → Account**.

Docker: `docker run -d -p 8000:8000 -v ark-data:/data ghcr.io/salehin-07/ark-storage:latest`, open http://localhost:8000, sign in with `ark` / `ark`;
the first sign-in opens a setup screen that asks for your repository and token (or pass `ARK_REPO_URL` / `ARK_GITHUB_TOKEN` as environment variables, or use `docker compose up -d`).
Render: use `render.yaml` (a Docker web service), set the two variables, done. See [docs/deploy.md](docs/deploy.md).

## Configuration

Only `ARK_REPO_URL` and `ARK_GITHUB_TOKEN` are required. Everything else is optional and editable in **Settings**
(values saved there win over the environment).

| Variable | Default | Meaning |
|---|---|---|
| `ARK_REPO_URL` | – | `https://github.com/owner/repo` (or `owner/repo`) |
| `ARK_GITHUB_TOKEN` | – | token with Contents read & write |
| `ARK_BRANCH` | `ark-data` | storage branch, created automatically, keeps data apart from code |
| `ARK_ENCRYPT` | `false` | encrypt newly stored files |
| `ARK_PASSPHRASE` | – | needed for encryption; never stored in the repo |
| `ARK_CHUNK_MB` | `48` | piece size for large files (1–95) |
| `ARK_CACHE_MB` | `1024` | disk cache of downloaded pieces |
| `ARK_MAX_UPLOAD_MB` | `20480` | per-file upload limit, `0` = unlimited |
| `ARK_REFRESH_SECONDS` | `10` | how long a listing is trusted before re-checking GitHub |
| `ARK_TRASH` / `ARK_SHOW_HIDDEN` | `true` / `false` | behaviour toggles |
| `ARK_SHARE_HOURS` | `24` | default share-link lifetime |
| `ARK_USERNAME` / `ARK_PASSWORD` | `ark` / `ark` | initial login (first start only) |
| `ARK_SECRET_KEY` | generated | keeps sessions and share links valid across restarts and redeploys |
| `ARK_DATA_DIR` | `./data` | settings, cache and temporary files |
| `PORT` | `8000` | listen port |

## How it works

Reads use the GitHub REST API (one conditional request refreshes the listing; blobs are cached on disk). Writes use plain `git`:
a batch of files becomes **one commit and one push**, so uploading a folder of 500 files does not trip GitHub's
content-creation rate limits. Renames, moves and copies reuse existing blobs, so they never re-upload data.

Your files stay visible and diffable on GitHub, at their real paths under `files/`:

```
files/photos/cat.jpg                    a normal file
files/video/movie.mkv.arkparts/0000     a large file, split into pieces
files/notes/secret.txt.arkenc           an encrypted file
files/empty-folder/.arkkeep             keeps an empty folder alive
trash/<id>/...                          deleted items until you empty the trash
.ark/{meta,trash,config}.json           modification times, trash index, encryption salt (no secrets)
```

Details: [docs/architecture.md](docs/architecture.md), [docs/security.md](docs/security.md).

## Good to know

- **Public repo = public files** unless encryption is on. The app warns you. Encryption hides contents, **not file names**.
- **A lost passphrase cannot be recovered.**
- Deleting a file removes it from the current tree, but git history still holds the data (and counts toward the repo size).
  **Settings → Maintenance → Compact history** squashes history to reclaim space. GitHub recommends repositories stay under ~5 GB.
- Free hosts with ephemeral disks (Render free) lose the cache and settings on redeploy. Set `ARK_SECRET_KEY`,
  and keep your settings in environment variables; your *files* are in GitHub and are never lost.
- Uploads **stream to GitHub while they arrive**: the server needs only a few hundred MB of free disk and well under 300 MB of RAM, however big the file is (tune with *Push batch* in Settings → Performance, `ARK_STAGE_MB`).
- Only `.zip` can be extracted in the browser. Names ending in `.arkparts` / `.arkenc`, called `.arkkeep` or `.git` are reserved.

## Development

```bash
python -m unittest discover -s tests -t .        # 20+ tests, no network needed (a local git repo stands in for GitHub)
npm i jsdom && JSDOM_PATH=node_modules/jsdom node tests/ui_smoke.js
```

MIT licensed. Created by [Salehin-07](https://github.com/Salehin-07). The selection and editor logic come from
[rconsole-ark](https://github.com/Salehin-07/rconsole-ark).
