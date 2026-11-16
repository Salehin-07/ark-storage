"""Flask application: JSON API + the single-page file browser."""
import base64
import contextlib
import functools
import gzip
import io
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, request, send_file, send_from_directory, session
from itsdangerous import BadSignature, URLSafeSerializer

from . import __version__, auth, config, crypto, office, vfs, zipstream
from .gh import GitHubError
from .jobs import Jobs
from .store import Ingest, Store
from .vfs import VfsError

STATIC = Path(__file__).parent / "static"
COMPRESSIBLE = {"application/json", "application/javascript", "application/manifest+json", "image/svg+xml"}
TEXT_LIMIT = 5 * 1024 * 1024
KINDS = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif", "svg", "tif", "tiff", "heic"},
    "video": {"mp4", "m4v", "webm", "ogv", "mov", "mkv", "avi"},
    "audio": {"mp3", "wav", "ogg", "oga", "opus", "m4a", "aac", "flac", "weba"},
    "pdf": {"pdf"},
    "archive": {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "zst", "jar", "apk", "iso", "dmg"},
    "binary": {"exe", "dll", "so", "bin", "dat", "class", "pyc", "o", "wasm", "ttf", "otf", "woff", "woff2",
               "db", "sqlite", "psd", "img"},
    # Word / Excel / PowerPoint / OpenDocument: shown in the browser (see static/office.js), not downloaded
    "office": {"docx", "docm", "dotx", "dotm", "doc", "dot", "rtf", "odt", "xlsx", "xlsm", "xlsb", "xls", "ods",
               "pptx", "pptm", "ppsx", "ppt", "pps", "odp"},
}
# Formats the browser renders itself; every other "office" type is converted to PDF on the server (needs LibreOffice).
OFFICE_CLIENT = {"docx", "docm", "xlsx", "xlsm", "xlsb", "xls", "ods", "pptx", "pptm", "ppsx"}
EXT_KIND = {e: k for k, exts in KINDS.items() for e in exts}
# Browsers may run scripts from these when served from our origin, so they get a sandbox CSP.
ACTIVE = {"html", "htm", "xhtml", "svg", "xml", "js", "mjs"}
EXTRA_MIME = {"md": "text/plain", "py": "text/plain", "json": "application/json", "mkv": "video/x-matroska",
              "m4a": "audio/mp4", "opus": "audio/ogg", "flac": "audio/flac", "avif": "image/avif",
              "webp": "image/webp", "heic": "image/heic",
              "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "docm": "application/vnd.ms-word.document.macroEnabled.12", "doc": "application/msword",
              "dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template", "dot": "application/msword",
              "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12", "xls": "application/vnd.ms-excel",
              "xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
              "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
              "ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
              "ppt": "application/vnd.ms-powerpoint", "pps": "application/vnd.ms-powerpoint", "rtf": "application/rtf",
              "odt": "application/vnd.oasis.opendocument.text", "ods": "application/vnd.oasis.opendocument.spreadsheet",
              "odp": "application/vnd.oasis.opendocument.presentation"}


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_of(name):
    return EXT_KIND.get(ext_of(name), "text" if ext_of(name) not in ("",) else "file")


def mime_of(name):
    return EXTRA_MIME.get(ext_of(name)) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def create_app():
    app = Flask(__name__, static_folder=None)
    app.secret_key = config.secret_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_NAME="ark_session",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=config.get("session_hours")))
    store = Store()
    jobs = Jobs()
    uploads = {}
    up_lock = threading.Lock()
    info_cache = {"t": 0, "v": None}
    shares = URLSafeSerializer(app.secret_key, salt="ark-share")
    app.ark_store, app.ark_jobs = store, jobs

    # ---------------------------------------------------------------- helpers
    def api(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not session.get("u"):
                return jsonify(error="Please sign in", login=True), 401
            if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
                    request.headers.get("X-CSRF-Token", ""), session.get("csrf", "x")):
                return jsonify(error="Session expired, reload the page"), 403
            try:
                return fn(*a, **kw)
            except VfsError as e:
                return jsonify(error=str(e)), e.status
            except crypto.CryptoError as e:
                return jsonify(error=str(e)), 423
            except GitHubError as e:
                return jsonify(error=str(e)), 502
        return wrapper

    def body():
        return request.get_json(silent=True) or {}

    def entry(n, children=None):
        return {"name": n.name, "path": n.path, "dir": n.is_dir, "size": n.size, "mtime": n.mtime, "enc": n.enc,
                "parts": len(n.parts), "mime": "" if n.is_dir else mime_of(n.name),
                "kind": "dir" if n.is_dir else kind_of(n.name),
                "items": len(children) if n.is_dir and children is not None else None}

    def idx():
        return store.refresh()

    def need_file(path):
        n = idx().files.get(vfs.norm(path))
        if not n:
            raise VfsError("Not found", 404)
        if n.is_dir:
            raise VfsError("That is a folder", 400)
        return n

    def need_node(path):
        n = idx().files.get(vfs.norm(path))
        if not n:
            raise VfsError("Not found", 404)
        return n

    def repo_info():
        if time.time() - info_cache["t"] > 60:
            info_cache["v"] = store.repo_info()
            info_cache["t"] = time.time()
        return info_cache["v"]

    gz_memo = {}

    def squeeze(r):
        """gzip text responses (JSON listings, JS, CSS, HTML). Streams, ranges and media are left alone;
        static files are compressed once per version and served from memory after that."""
        if (r.status_code != 200 or r.is_streamed and not r.direct_passthrough or "Content-Encoding" in r.headers
                or "gzip" not in request.headers.get("Accept-Encoding", "")
                or not (r.mimetype.startswith("text/") or r.mimetype in COMPRESSIBLE)):
            return r
        static = request.path.startswith("/static/") and r.direct_passthrough
        if r.direct_passthrough and not static:
            return r
        key = (request.path, r.headers.get("ETag")) if static and r.headers.get("ETag") else None
        z = gz_memo.get(key) if key else None
        if z is None:
            if static:
                r.direct_passthrough = False
            data = r.get_data()
            if len(data) < 1024:
                return r
            z = gzip.compress(data, compresslevel=6, mtime=0)
            if key:
                gz_memo[key] = z
        r.set_data(z)
        r.headers["Content-Encoding"] = "gzip"
        r.headers["Content-Length"] = str(len(z))
        r.headers.add("Vary", "Accept-Encoding")
        return r

    @app.after_request
    def headers(r):
        r.headers.setdefault("X-Content-Type-Options", "nosniff")
        r.headers.setdefault("Referrer-Policy", "same-origin")
        r.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.path.startswith("/api/") and "Cache-Control" not in r.headers:
            r.headers["Cache-Control"] = "no-store"
        return squeeze(r)

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text("utf-8").replace("{{v}}", __version__)
        r = make_response(html)
        r.headers["Content-Type"] = "text/html; charset=utf-8"
        r.headers["Cache-Control"] = "no-cache"
        r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                                "style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'self'; "
                                                "base-uri 'none'; form-action 'self'")
        return r

    @app.get("/static/<path:name>")
    def static_files(name):
        r = send_from_directory(STATIC, name)
        # /static/app.js?v=1.0.2 changes whenever the release does, so a versioned URL can be cached for good.
        r.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.args.get("v") else "no-cache"
        return r

    @app.get("/favicon.ico")
    def favicon():
        r = send_from_directory(STATIC, "icon-32.png")
        r.headers["Cache-Control"] = "public, max-age=86400"
        return r

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, version=__version__)

    @app.get("/manifest.webmanifest")
    def manifest():
        return jsonify(name="Ark Storage", short_name="Ark", start_url="/", display="standalone",
                       background_color="#0f1115", theme_color="#0f1115",
                       icons=[{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                              {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                              {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}])

    # ---------------------------------------------------------------- session
    @app.get("/api/session")
    def get_session():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        a = auth.load()
        return jsonify(authed=bool(session.get("u")), user=session.get("u"), csrf=session["csrf"],
                       default_password=bool(a.get("default")) and bool(session.get("u")), version=__version__,
                       theme=config.get("theme"), office_convert=office.available())

    @app.post("/api/login")
    def login():
        d = body()
        ok, wait = auth.verify(str(d.get("username", "")), str(d.get("password", "")), request.remote_addr)
        if wait:
            return jsonify(error=f"Too many attempts. Try again in {wait} seconds."), 429
        if not ok:
            time.sleep(0.4)
            return jsonify(error="Wrong username or password"), 401
        session.clear()
        session.permanent = bool(d.get("remember", True))
        session["u"] = auth.load()["username"]
        session["csrf"] = secrets.token_hex(16)
        return jsonify(ok=True, csrf=session["csrf"], user=session["u"], default_password=bool(auth.load().get("default")))

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/account")
    @api
    def account():
        d = body()
        try:
            a = auth.change(d.get("current", ""), (d.get("username") or "").strip() or None, d.get("password") or None)
        except ValueError as e:
            raise VfsError(str(e)) from None
        session["u"] = a["username"]
        return jsonify(ok=True, user=a["username"])

    # ---------------------------------------------------------------- browsing
    @app.get("/api/list")
    @api
    def list_dir():
        path = vfs.norm(request.args.get("path", "/"))
        i = idx()
        n = i.files.get(path)
        if not n:
            raise VfsError("Folder not found", 404)
        if not n.is_dir:
            raise VfsError("That is a file", 400)
        hidden = request.args.get("hidden") == "1" or config.get("show_hidden")
        kids = [k for k in i.files.ls(path) if hidden or not k.name.startswith(".")]
        kids.sort(key=lambda k: (not k.is_dir, k.name.lower()))
        crumbs, acc = [{"name": "Home", "path": "/"}], ""
        for seg in [s for s in path.split("/") if s]:
            acc += "/" + seg
            crumbs.append({"name": seg, "path": acc})
        return jsonify(path=path, parent=None if path == "/" else vfs.parent(path), crumbs=crumbs,
                       entries=[entry(k, i.files.children.get(k.path, ())) for k in kids], head=i.key[:7])

    @app.get("/api/search")
    @api
    def search():
        q = request.args.get("q", "").strip().lower()
        base = vfs.norm(request.args.get("path", "/"))
        if not q:
            return jsonify(entries=[])
        out = []
        i = idx()
        for n in i.files.walk(base):
            if q in n.name.lower() and not any(s.startswith(".") for s in n.path.split("/") if s):
                out.append(entry(n, i.files.children.get(n.path, ())))
                if len(out) >= 500:
                    break
        return jsonify(entries=out, truncated=len(out) >= 500)

    @app.get("/api/stat")
    @api
    def stat():
        n = need_node(request.args.get("path", "/"))
        i = idx()
        d = entry(n, i.files.children.get(n.path, ()))
        if n.is_dir:
            sub = list(i.files.walk(n.path))
            d.update(files=sum(1 for s in sub if not s.is_dir), folders=sum(1 for s in sub if s.is_dir),
                     size=sum(s.size for s in sub if not s.is_dir))
        else:
            d.update(stored=n.stored_size, sha=n.sha, repo_path=n.repo, pieces=len(n.parts))
        return jsonify(d)

    @app.post("/api/exists")
    @api
    def exists():
        i = idx()
        return jsonify(existing=[p for p in map(vfs.norm, body().get("paths", [])) if i.files.get(p)])

    # ---------------------------------------------------------------- reading
    def parse_range(header, size):
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
        if not m or (not m.group(1) and not m.group(2)):
            return None
        if m.group(1):
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else size - 1
        else:
            a, b = max(0, size - int(m.group(2))), size - 1
        b = min(b, size - 1)
        if a > b or a >= size:
            return False
        return a, b

    def serve_file(n, download=False, i=None):
        i = i or idx()
        reader = store.reader(n, i)
        size = n.size
        ct = mime_of(n.name)
        h = {"Accept-Ranges": "bytes", "ETag": f'"{n.sha[:16]}"', "Cache-Control": "private, max-age=0, must-revalidate"}
        quoted = n.name.replace('"', "")
        ascii_name = quoted.encode("ascii", "replace").decode().replace("?", "_")
        from urllib.parse import quote
        h["Content-Disposition"] = (f'{"attachment" if download else "inline"}; filename="{ascii_name}"; '
                                    f"filename*=UTF-8''{quote(n.name)}")
        if ext_of(n.name) in ACTIVE:
            h["Content-Security-Policy"] = "sandbox"
        if request.headers.get("If-None-Match") == h["ETag"] and not request.headers.get("Range"):
            return Response(status=304, headers=h)
        rng = parse_range(request.headers.get("Range"), size) if size else None
        if rng is False:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        # verify the first block now so a wrong passphrase is a clean error, not a broken stream
        if rng:
            a, b = rng
            gen = reader.iter(a, b + 1)
            h["Content-Range"] = f"bytes {a}-{b}/{size}"
            h["Content-Length"] = str(b - a + 1)
            status = 206
        else:
            gen = reader.iter(0, size)
            h["Content-Length"] = str(size)
            status = 200
        first = next(gen, b"")

        def chain():
            if first:
                yield first
            yield from gen
        return Response(chain(), status=status, headers=h, mimetype=ct)

    @app.get("/api/raw")
    @api
    def raw():
        return serve_file(need_file(request.args.get("path", "")), download=request.args.get("download") == "1")

    @app.get("/api/preview-pdf")
    @api
    def preview_pdf():
        """Old Office formats (.doc, .ppt, .odt, ...) as a PDF, converted once with LibreOffice when it is installed."""
        n = need_file(request.args.get("path", ""))
        if EXT_KIND.get(ext_of(n.name)) != "office" or ext_of(n.name) in OFFICE_CLIENT:
            raise VfsError("This file is shown directly in the browser", 400)
        path, data = office.to_pdf(n, store.reader(n, idx()))
        if path:
            r = send_file(path, mimetype="application/pdf", conditional=True, etag=f"{n.sha[:16]}p", max_age=0)
        else:
            r = send_file(io.BytesIO(data), mimetype="application/pdf", conditional=True, etag=False, max_age=0)
        r.headers["Cache-Control"] = "private, max-age=0, must-revalidate" if path else "no-store"
        return r

    @app.post("/api/warm")
    @api
    def warm():
        """Start downloading the first pieces of a file in the background (called when the pointer rests on it)."""
        n = need_file(body().get("path", ""))
        store.prefetch([p[1] for p in n.parts[:3]])
        return Response(status=204)

    @app.get("/api/zip")
    @api
    def zip_download():
        paths = request.args.getlist("p")
        if not paths:
            raise VfsError("Nothing selected")
        i = idx()
        nodes = [need_node(p) for p in paths]
        name = (request.args.get("name") or (nodes[0].name if len(nodes) == 1 else "download")) + ".zip"
        gen = zip_entries(i, nodes)
        from urllib.parse import quote
        return Response(zipstream.stream(gen), mimetype="application/zip", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "Cache-Control": "no-store"})

    def zip_entries(i, nodes):
        def factory(n):
            return lambda: store.reader(n, i).iter()
        for n in nodes:
            if not n.is_dir:
                yield (n.name, n.mtime, factory(n))
                continue
            base = n.path.rstrip("/")
            yield (n.name + "/", 0, None)
            for d in i.files.walk(n.path):
                rel = n.name + d.path[len(base):]
                yield (rel + "/", 0, None) if d.is_dir else (rel, d.mtime, factory(d))

    @app.get("/api/text")
    @api
    def read_text():
        n = need_file(request.args.get("path", ""))
        if EXT_KIND.get(ext_of(n.name)) in ("archive", "binary", "image", "video", "audio", "pdf", "office"):
            return jsonify(error="This looks like a binary or compressed file, so it can't be edited as text.", binary=True), 415
        if n.size > TEXT_LIMIT:
            return jsonify(error=f"File is larger than {TEXT_LIMIT // 1048576} MB; download it instead.", binary=True), 413
        data = store.reader(n, idx()).read_all(TEXT_LIMIT)
        if b"\x00" in data[:8192]:
            return jsonify(error="This file contains binary data, so it can't be edited as text.", binary=True), 415
        return jsonify(path=n.path, content=data.decode("utf-8", "replace"), size=n.size, sha=n.sha)

    @app.put("/api/text")
    @api
    def write_text():
        d = body()
        path = vfs.norm(d.get("path", ""))
        i = idx()
        cur = i.files.get(path)
        if cur and cur.is_dir:
            raise VfsError("That is a folder")
        if cur and d.get("sha") and d["sha"] != cur.sha and not d.get("force"):
            raise VfsError("This file changed since you opened it. Reload it, or save again to overwrite.", 409)
        data = str(d.get("content", "")).encode("utf-8")
        with store.tx() as tx:
            tx.put_bytes(path, data, policy="replace", enc=cur.enc if cur else None)
            store.commit(tx, f"Edit {vfs.basename(path)}")
        n = store.idx.files.get(path)
        return jsonify(ok=True, sha=n.sha if n else "", size=len(data))

    # ---------------------------------------------------------------- changing things
    def run_tx(message, fn):
        with store.tx() as tx:
            fn(tx)
            return store.commit(tx, message)

    @app.post("/api/mkdir")
    @api
    def mkdir():
        p = vfs.norm(body().get("path", ""))
        r = run_tx(f"Create folder {vfs.basename(p)}", lambda tx: tx.mkdir(p))
        return jsonify(ok=True, results=r)

    @app.post("/api/newfile")
    @api
    def newfile():
        d = body()
        p = vfs.norm(d.get("path", ""))
        if idx().files.get(p):
            raise VfsError("A file or folder with that name already exists", 409)
        r = run_tx(f"Create {vfs.basename(p)}", lambda tx: tx.put_bytes(p, str(d.get("content", "")).encode(), policy="fail"))
        return jsonify(ok=True, results=r)

    @app.post("/api/rename")
    @api
    def rename():
        d = body()
        p = vfs.norm(d.get("path", ""))
        name = vfs.validate_name(str(d.get("name", "")).strip())
        n = need_node(p)
        r = run_tx(f"Rename {n.name} to {name}", lambda tx: tx.move([p], vfs.parent(p), policy="fail", newname=name))
        return jsonify(ok=True, results=r)

    @app.post("/api/move")
    @api
    def move():
        d = body()
        copy = bool(d.get("copy"))
        paths = d.get("paths", [])
        r = run_tx(f"{'Copy' if copy else 'Move'} {len(paths)} item(s)",
                   lambda tx: tx.move(paths, d.get("dest", "/"), policy=d.get("on_conflict", "rename"), copy=copy))
        return jsonify(ok=True, results=r)

    @app.post("/api/delete")
    @api
    def delete():
        d = body()
        paths = d.get("paths", [])
        r = run_tx(f"Delete {len(paths)} item(s)", lambda tx: tx.delete(paths, permanent=bool(d.get("permanent"))))
        return jsonify(ok=True, results=r, trashed=config.get("trash") and not d.get("permanent"))

    @app.get("/api/trash")
    @api
    def trash_list():
        i = idx()
        out = []
        for tid, m in i.trash_meta.items():
            if i.trash.ls("/" + tid):
                out.append({"id": tid, "name": m["name"], "path": m["path"], "dir": m["dir"], "ts": m["ts"], "size": m.get("size", 0)})
        out.sort(key=lambda x: -x["ts"])
        return jsonify(items=out)

    @app.post("/api/trash/restore")
    @api
    def trash_restore():
        ids = body().get("ids", [])
        r = run_tx(f"Restore {len(ids)} item(s)", lambda tx: tx.restore(ids))
        return jsonify(ok=True, results=r)

    @app.post("/api/trash/empty")
    @api
    def trash_empty():
        ids = body().get("ids")
        run_tx("Empty trash" if not ids else "Delete from trash", lambda tx: tx.empty_trash(ids))
        return jsonify(ok=True)

    # ---------------------------------------------------------------- uploads
    # Chunks may arrive in parallel and out of order. They are ingested strictly in order; every finished
    # piece is hashed straight into git and pushed in rolling batches, so disk use stays flat.
    UP_CHUNK = 16 * 1048576          # what the web client sends per request (4 in flight)

    def batch_dir(bid):
        if not re.fullmatch(r"[0-9a-f]{16}", bid or ""):
            raise VfsError("Bad upload id", 400)
        return config.DATA_DIR / "uploads" / bid

    def end_batch(bid, u):
        u["ing"].abort()
        u["ing"].close()
        shutil.rmtree(batch_dir(bid), ignore_errors=True)

    def get_batch(bid):
        with up_lock:
            u = uploads.get(bid)
        if u is None:
            raise VfsError("Unknown upload", 404)
        return u

    @app.post("/api/upload/begin")
    @api
    def upload_begin():
        files = body().get("files", [])
        limit = config.get("max_upload_mb") * 1048576
        total = 0
        for f in files:
            sz = int(f.get("size", 0))
            if limit and sz > limit:
                raise VfsError(f"'{f.get('name', 'file')}' is larger than the upload limit "
                               f"({config.get('max_upload_mb')} MB). Raise it in Settings.", 413)
            total += sz
        root = config.DATA_DIR / "uploads"
        root.mkdir(parents=True, exist_ok=True)
        now = time.time()
        with up_lock:
            stale = [(k, uploads.pop(k)) for k in [k for k, u in uploads.items() if now - u["t"] > 6 * 3600]]
            live = set(uploads)
        for k, u in stale:
            end_batch(k, u)
        for old in root.iterdir():                       # abandoned directories (e.g. after a restart)
            if old.name not in live and now - old.stat().st_mtime > 3600:
                shutil.rmtree(old, ignore_errors=True)
        for old in config.DATA_DIR.glob("ark-obj-*"):
            if now - old.stat().st_mtime > 6 * 3600:
                shutil.rmtree(old, ignore_errors=True)
        if not live:
            with contextlib.suppress(Exception):                # several worker processes: wait until a batch is seen idle twice
                store.sweep_staging(live, confirm=int(os.environ.get("ARK_WORKERS", "1") or 1) > 1)
        chunk = config.get("chunk_mb") * 1048576
        need = min(total, config.get("stage_mb") * 1048576 + 3 * chunk) + 4 * UP_CHUNK + 64 * 1048576
        free = shutil.disk_usage(root).free
        if need > free:
            raise VfsError(f"Not enough free disk space on the server for this upload: it needs about "
                           f"{need // 1048576} MB free (it streams to GitHub, so this does not grow with the file). "
                           f"Lower 'Push batch' in Settings -> Performance to need less.", 507)
        bid = secrets.token_hex(8)
        ing = Ingest(store, bid)
        (root / bid).mkdir()
        fs = {str(f["id"]): {"size": int(f.get("size", 0)), "got": 0, "st": None, "pend": {}, "parts": None,
                             "err": "", "lock": threading.Lock()} for f in files}
        with up_lock:
            uploads[bid] = {"ing": ing, "files": fs, "t": now}
        return jsonify(batch=bid, chunk=UP_CHUNK)

    @app.put("/api/upload/<bid>/<fid>")
    @api
    def upload_chunk(bid, fid):
        u = get_batch(bid)
        F = u["files"].get(fid)
        d = batch_dir(bid)
        if F is None or not d.exists():
            raise VfsError("Unknown upload", 404)
        offset = int(request.args.get("offset", 0))
        safe = re.sub(r"\W", "_", fid)
        tmp = d / f"{safe}.{offset}"
        n = 0
        try:
            with open(tmp, "wb") as f:
                while True:
                    blk = request.stream.read(1 << 20)
                    if not blk:
                        break
                    f.write(blk)
                    n += len(blk)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise
        if offset + n > F["size"]:
            os.remove(tmp)
            raise VfsError("Received more data than announced", 400)
        with F["lock"]:
            if F["err"]:
                os.remove(tmp)
                raise VfsError(F["err"], 500)
            if F["parts"] is not None or offset < F["got"]:          # a retry of data we already hold
                with contextlib.suppress(OSError):
                    os.remove(tmp)
                return jsonify(ok=True, got=F["got"])
            F["pend"][offset] = (tmp, n)
            try:
                if F["st"] is None:
                    F["st"] = u["ing"].stager(safe)
                while F["got"] in F["pend"]:
                    path, k = F["pend"].pop(F["got"])
                    with open(path, "rb") as src:
                        while True:
                            blk = src.read(1 << 20)
                            if not blk:
                                break
                            F["st"].write(blk)
                    os.remove(path)
                    F["got"] += k
                if F["got"] == F["size"]:
                    F["parts"] = F["st"].finish()
            except Exception as e:
                F["err"] = f"Could not process the upload: {e}"
                raise VfsError(F["err"], 500) from None
            got = F["got"]
        u["ing"].maybe_flush()
        return jsonify(ok=True, got=got)

    @app.post("/api/upload/<bid>/commit")
    @api
    def upload_commit(bid):
        u = get_batch(bid)
        ing = u["ing"]
        data = body()
        files = data.get("files", [])
        policy = data.get("on_conflict", "rename")
        plan = []
        for f in files:
            F = u["files"].get(str(f["id"]))
            if F is None or F["parts"] is None:
                raise VfsError(f"'{f.get('path')}' was not fully received", 400)
            path = vfs.norm(f["path"])
            vfs.validate_name(vfs.basename(path))
            plan.append((path, F["parts"], f.get("mtime"), F["size"]))
        empty_dirs = [vfs.norm(p) for p in data.get("dirs", [])]
        title = f"Upload {len(plan)} file(s)" if len(plan) != 1 else f"Upload {vfs.basename(plan[0][0])}"

        def run(job):
            ok = False
            try:
                with store.tx(ingest=ing) as tx:
                    for path, parts, mtime, size in plan:
                        tx.put_staged(path, parts, ing.enc, mtime=(mtime / 1000 if mtime else None), policy=policy, expect=size)
                    for dd in empty_dirs:
                        tx.mkdir(dd)
                    job.update(0.85, "Pushing to GitHub")
                    res = store.commit(tx, title)
                    ing.staged.clear()
                    ok = True
                    return res
            finally:
                if not ok:
                    ing.abort()
                ing.close()
                shutil.rmtree(batch_dir(bid), ignore_errors=True)
                with up_lock:
                    uploads.pop(bid, None)
        job = jobs.start("upload", title, run)
        return jsonify(job=job.to_dict())

    @app.post("/api/upload/<bid>/cancel")
    @api
    def upload_cancel(bid):
        with up_lock:
            u = uploads.pop(bid, None)
        if u:
            end_batch(bid, u)
        return jsonify(ok=True)

    # ---------------------------------------------------------------- compress / extract
    @app.post("/api/compress")
    @api
    def compress():
        d = body()
        paths = d.get("paths", [])
        dest = vfs.norm(d.get("dest", "/"))
        name = vfs.validate_name(str(d.get("name") or "archive.zip"))
        i = idx()
        nodes = [need_node(p) for p in paths]

        def run(job):
            tmpf = tempfile.NamedTemporaryFile(dir=config.DATA_DIR, delete=False, prefix="zip-")
            try:
                with tmpf:
                    for k, chunk in enumerate(zipstream.stream(zip_entries(i, nodes))):
                        tmpf.write(chunk)
                        job.update(min(0.7, k / 4000), "Compressing")
                with store.tx() as tx:
                    job.update(0.75, "Storing archive")
                    tx.put(vfs.join(dest, name), tmpf.name, policy=d.get("on_conflict", "rename"))
                    return store.commit(tx, f"Compress into {name}")
            finally:
                os.remove(tmpf.name)
        return jsonify(job=jobs.start("compress", f"Compress to {name}", run).to_dict())

    @app.post("/api/extract")
    @api
    def extract():
        d = body()
        n = need_file(d.get("path", ""))
        if ext_of(n.name) != "zip":
            raise VfsError("Only .zip archives can be extracted here")
        stem = n.name[:-4] or "extracted"
        dest = vfs.norm(d.get("dest") or vfs.join(vfs.parent(n.path), stem))
        i = idx()
        limit = max(1, config.get("max_upload_mb")) * 1048576

        def run(job):
            tmp = tempfile.NamedTemporaryFile(dir=config.DATA_DIR, delete=False, prefix="unzip-")
            skipped = 0
            try:
                with tmp:
                    for chunk in store.reader(n, i).iter():
                        tmp.write(chunk)
                with zipfile.ZipFile(tmp.name) as z:
                    infos = z.infolist()
                    if len(infos) > 20000 or sum(x.file_size for x in infos) > limit:
                        raise VfsError("Archive is too large to extract here")
                    with store.tx() as tx:
                        for k, info in enumerate(infos):
                            job.update(k / max(1, len(infos)) * 0.8, f"Extracting {info.filename}")
                            try:
                                parts = [vfs.validate_name(s) for s in info.filename.split("/") if s]
                            except VfsError:
                                skipped += 1
                                continue
                            if not parts or info.filename.startswith("/") or ".." in parts:
                                skipped += 1
                                continue
                            target = dest + "/" + "/".join(parts)
                            if info.is_dir():
                                tx.mkdir(target)
                                continue
                            out = os.path.join(tx.w.objdir, "x.tmp")
                            with z.open(info) as src, open(out, "wb") as dst:
                                shutil.copyfileobj(src, dst, 1 << 20)
                            tx.put(target, out, mtime=time.mktime(info.date_time + (0, 0, -1)), policy=d.get("on_conflict", "rename"))
                            os.remove(out)
                        job.update(0.9, "Pushing to GitHub")
                        store.commit(tx, f"Extract {n.name}")
                return {"dest": dest, "skipped": skipped}
            finally:
                os.remove(tmp.name)
        return jsonify(job=jobs.start("extract", f"Extract {n.name}", run).to_dict())

    # ---------------------------------------------------------------- sharing
    @app.post("/api/share")
    @api
    def share():
        d = body()
        n = need_file(d.get("path", ""))
        hours = int(d.get("hours") or config.get("share_hours"))
        token = shares.dumps({"p": n.path, "e": int(time.time() + hours * 3600)})
        return jsonify(url=request.host_url.rstrip("/") + "/s/" + token, hours=hours)

    @app.get("/s/<token>")
    def shared(token):
        try:
            d = shares.loads(token)
            if d["e"] < time.time():
                return "This link has expired.", 410
            n = store.refresh().files.get(d["p"])
            if not n or n.is_dir:
                return "This file no longer exists.", 404
            return serve_file(n, download=request.args.get("inline") != "1")
        except BadSignature:
            return "Invalid link.", 404
        except VfsError as e:
            return str(e), e.status

    # ---------------------------------------------------------------- jobs, status, settings
    @app.get("/api/jobs")
    @api
    def jobs_list():
        return jsonify(jobs=jobs.list())

    @app.get("/api/jobs/<jid>")
    @api
    def job_get(jid):
        j = jobs.get(jid)
        if not j:
            raise VfsError("Unknown job", 404)
        return jsonify(j.to_dict())

    @app.get("/api/status")
    @api
    def status():
        out = {"version": __version__, "problems": store.problems(), "branch": config.get("branch"),
               "repo_url": config.get("repo_url"), "git": bool(shutil.which("git")), "crypto": store.crypto_state(),
               "cache_bytes": store.cache.size(), "last_error": store.last_error, "trash": bool(config.get("trash")),
               "office_convert": office.available()}
        if not out["problems"]:
            try:
                info = repo_info()
                out.update(repo=info["full_name"], private=info["private"], repo_kb=info.get("size", 0),
                           can_push=bool((info.get("permissions") or {}).get("push", True)),
                           stats=store.stats(), crypto=store.crypto_state())
            except (VfsError, GitHubError) as e:
                out["error"] = str(e)
        return jsonify(out)

    @app.get("/api/settings")
    @api
    def settings_get():
        return jsonify(settings=config.public_view(), problems=store.problems())

    @app.put("/api/settings")
    @api
    def settings_put():
        changes = body().get("changes", {})
        for k, v in list(changes.items()):
            if k not in config.SCHEMA:
                raise VfsError(f"Unknown setting: {k}")
            if config.SCHEMA[k][3] and v == "":
                changes.pop(k)               # blank secret box = leave unchanged
        try:
            for k, v in changes.items():
                config.validate(k, v)
        except ValueError as e:
            raise VfsError(str(e)) from None
        phrase = changes.get("passphrase")
        if phrase:
            conf = store.idx.conf if store.idx else {}
            if conf.get("salt"):
                key = crypto.derive_master(phrase, base64.b64decode(conf["salt"]))
                try:
                    ok = crypto.decrypt_bytes(key, base64.b64decode(conf["check"])) == b"ark-storage"
                except crypto.CryptoError:
                    ok = False
                if not ok:
                    raise VfsError("That passphrase does not match the one this repository was encrypted with.")
        try:
            config.update(changes)
        except ValueError as e:
            raise VfsError(str(e)) from None
        if {"repo_url", "token", "branch", "passphrase"} & set(changes):
            store.reset()
            info_cache["t"] = 0
        return jsonify(ok=True, settings=config.public_view(), problems=store.problems())

    @app.post("/api/settings/test")
    @api
    def settings_test():
        store.reset()
        info_cache["t"] = 0
        i = store.refresh(force=True)
        info = repo_info()
        return jsonify(ok=True, repo=info["full_name"], private=info["private"], head=i.key[:7],
                       can_push=bool((info.get("permissions") or {}).get("push", True)),
                       message="Connected. The storage branch is ready.")

    @app.post("/api/admin/<action>")
    @api
    def admin(action):
        d = body()
        if action == "clear-cache":
            store.cache.clear()
            return jsonify(ok=True)
        if action == "refresh":
            store.client._etags.clear()
            store.refresh(force=True)
            return jsonify(ok=True)
        if action == "cleanup":
            with up_lock:
                live = set(uploads)
            removed = store.sweep_staging(live)
            return jsonify(ok=True, removed=removed, incomplete=len(store.refresh().files.incomplete))
        if action == "squash":
            return jsonify(job=jobs.start("squash", "Compact history", lambda j: store.squash()).to_dict())
        if action == "convert":
            to = bool(d.get("encrypted"))
            if to and not config.get("passphrase"):
                raise VfsError("Set a passphrase first (Settings -> Encryption).")

            def run(job):
                if to:
                    store.master_for_write()
                return store.convert_all(to, lambda a, b, p: job.update(a / b, f"{a}/{b}  {p}"))
            title = "Encrypt all files" if to else "Decrypt all files"
            return jsonify(job=jobs.start("convert", title, run).to_dict())
        if action == "reset-crypto":
            i = idx()
            if any(n.enc for n in i.files.nodes.values() if not n.is_dir):
                raise VfsError("Some files are still encrypted. Decrypt them all first.")
            run_tx("Reset encryption setup", lambda tx: tx.set_conf({}))
            store._master = None
            return jsonify(ok=True)
        raise VfsError("Unknown action", 404)

    # Cleanup job: pieces pushed for an upload that never reached its manifest (crash, closed tab, failed commit) are
    # removed in the background. A batch must be seen idle and unchanged on two passes before it goes.
    def janitor():
        stop = threading.Event()
        stop.wait(300)
        while not stop.is_set():
            try:
                if not store.problems():
                    with up_lock:
                        live = set(uploads)
                    store.sweep_staging(live, confirm=True)
            except Exception:                                   # noqa: BLE001 - best effort, retried next round
                pass
            stop.wait(1800)

    if os.environ.get("ARK_JANITOR", "1") != "0" and not app.config.get("TESTING"):
        threading.Thread(target=janitor, daemon=True, name="ark-janitor").start()

    return app
