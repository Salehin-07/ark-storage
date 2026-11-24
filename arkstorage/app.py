"""FastAPI application: JSON API + the single-page file browser.

Everything on the request path is async. Anything that would block the event loop -- git subprocesses, hashing,
AES, deflate, walking a huge tree, password hashing -- is handed to a worker thread with ``aio.run``; bytes of
uploads and downloads move through the loop in 1 MiB steps with back-pressure from the socket.
"""
import asyncio
import contextlib
import gzip
import html as htmllib
import inspect
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import anyio
import orjson
from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response, StreamingResponse
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.datastructures import MutableHeaders

from . import __version__, aio, auth, config, crypto, office, vfs, zipstream
from . import schemas as S
from .gh import GitHubError
from .jobs import Jobs
from .session import SessionMiddleware
from .store import STAGE, Ingest, Store
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


def _human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_of(name):
    return EXT_KIND.get(ext_of(name), "text" if ext_of(name) not in ("",) else "file")


def mime_of(name):
    return EXTRA_MIME.get(ext_of(name)) or mimetypes.guess_type(name)[0] or "application/octet-stream"




CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "style-src 'self' 'unsafe-inline'; frame-src 'self'; object-src 'self'; base-uri 'none'; form-action 'self'")
UP_CHUNK = 16 * 1048576          # what the web client sends per request (4 in flight)


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


def _gz(data, level=4):
    return gzip.compress(data, compresslevel=level, mtime=0)


def raw_json(data, status=200):
    return Response(orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS), status_code=status, media_type="application/json")


async def jr(request, data, status=200, headers=None):
    """JSON response: serialised with orjson, gzip-compressed (off the loop when big) if the browser accepts it."""
    body = orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS)
    h = dict(headers or {})
    if len(body) > 1400 and "gzip" in request.headers.get("accept-encoding", ""):
        body = _gz(body) if len(body) < 65536 else await aio.run(_gz, body)
        h["Content-Encoding"] = "gzip"
        h["Vary"] = "Accept-Encoding"
    return Response(body, status_code=status, headers=h, media_type="application/json")


def sess(request):
    return request.scope["ark_session"]


async def require_auth(request: Request):
    s = sess(request)
    if not s.get("u"):
        raise ApiError(401, "Please sign in", login=True)
    if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
            request.headers.get("x-csrf-token", "").encode(), str(s.get("csrf", "x")).encode()):
        raise ApiError(403, "Session expired, reload the page")


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


class Polish:
    """Security headers on every response and ``no-store`` on the API (pure ASGI: no per-request task, streams untouched)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        api = scope["path"].startswith("/api/")

        async def wrapper(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "same-origin")
                h.setdefault("X-Frame-Options", "SAMEORIGIN")
                if api and "cache-control" not in h:
                    h["Cache-Control"] = "no-store"
            await send(message)
        await self.app(scope, receive, wrapper)


def create_app(testing=False):
    store = Store()
    jobs = Jobs()
    uploads = {}
    info_cache = {"t": 0, "v": None}
    secret = config.secret_key()
    shares = URLSafeSerializer(secret, salt="ark-share")
    static_memo = {}          # (path, mtime_ns, size) -> (etag, gzipped bytes): text assets are compressed once per version
    page = {"key": None, "html": ""}
    listings = {}             # (tree id, folder, hidden) -> [json bytes, gzip bytes or None]: a listing is rendered once per repository state

    # ---------------------------------------------------------------- background janitor
    # Pieces pushed for an upload that never reached its manifest (crash, closed tab, failed commit) are removed in the
    # background. A batch must be seen idle and unchanged on two passes before it goes.
    async def janitor():
        await asyncio.sleep(300)
        while True:
            try:
                if not store.problems():
                    await store.sweep_staging(set(uploads), confirm=True)
            except Exception:                                   # noqa: BLE001 - best effort, retried next round
                pass
            await asyncio.sleep(1800)

    @asynccontextmanager
    async def lifespan(_app):
        aio.set_threads(int(os.environ.get("ARK_THREADS") or 32))
        tasks = []
        if os.environ.get("ARK_JANITOR", "1") != "0" and not testing:
            tasks.append(asyncio.get_running_loop().create_task(janitor(), name="ark-janitor"))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await jobs.shutdown()
            await store.aclose()

    docs = os.environ.get("ARK_DOCS", "") in ("1", "true", "yes")
    app = FastAPI(title="Ark Storage", version=__version__, lifespan=lifespan,
                  docs_url="/api-docs" if docs else None, redoc_url=None, openapi_url="/api-docs.json" if docs else None)
    app.add_middleware(SessionMiddleware, secret=secret)
    app.add_middleware(Polish)
    app.ark_store, app.ark_jobs = store, jobs
    router = APIRouter(dependencies=[Depends(require_auth)])

    # ---------------------------------------------------------------- errors
    @app.exception_handler(ApiError)
    async def _api_error(_r, e):
        return raw_json({"error": str(e), **e.extra}, e.status)

    @app.exception_handler(VfsError)
    async def _vfs_error(_r, e):
        return raw_json({"error": str(e)}, e.status)

    @app.exception_handler(crypto.CryptoError)
    async def _crypto_error(_r, e):
        return raw_json({"error": str(e)}, 423)

    @app.exception_handler(GitHubError)
    async def _github_error(_r, e):
        return raw_json({"error": str(e)}, 502)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_r, e):
        first = (e.errors() or [{}])[0]
        where = ".".join(str(x) for x in first.get("loc", ())[1:])
        return raw_json({"error": f"Invalid request{': ' + where if where else ''} ({first.get('msg', 'bad value')})"}, 400)

    # ---------------------------------------------------------------- helpers
    def entry(n, children=None):
        return {"name": n.name, "path": n.path, "dir": n.is_dir, "size": n.size, "mtime": n.mtime, "enc": n.enc,
                "parts": len(n.parts), "mime": "" if n.is_dir else mime_of(n.name),
                "kind": "dir" if n.is_dir else kind_of(n.name),
                "items": len(children) if n.is_dir and children is not None else None}

    def node_of(i, path, file=False):
        n = i.files.get(vfs.norm(path))
        if not n:
            raise VfsError("Not found", 404)
        if file and n.is_dir:
            raise VfsError("That is a folder", 400)
        return n

    async def repo_info():
        if time.time() - info_cache["t"] > 60:
            info_cache["v"] = await store.repo_info()
            info_cache["t"] = time.time()
        return info_cache["v"]

    async def run_tx(message, fn):
        async with store.tx() as tx:
            r = fn(tx)
            if inspect.isawaitable(r):
                await r
            return await store.commit(tx, message)

    async def cpu(fn, size_hint=0):
        """Small jobs run inline (a thread hop costs more than they do); big ones go to a worker thread."""
        return fn() if size_hint < 400 else await aio.run(fn)

    # ---------------------------------------------------------------- pages
    def index_html():
        p = STATIC / "index.html"
        key = (p.stat().st_mtime_ns, __version__)
        if page["key"] != key:
            page["html"], page["key"] = p.read_text("utf-8").replace("{{v}}", __version__), key
        return page["html"]

    @app.api_route("/", methods=["GET", "HEAD"])
    async def index():
        return Response(index_html(), media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})

    # Clean client-side routes (/browse/Photos, /trash): the same page, so reloads and shared links land in the right place.
    @app.api_route("/browse", methods=["GET", "HEAD"])
    @app.api_route("/browse/{rest:path}", methods=["GET", "HEAD"])
    @app.api_route("/trash", methods=["GET", "HEAD"])
    async def spa_routes(rest: str = ""):
        return await index()

    @app.api_route("/static/{name:path}", methods=["GET", "HEAD"])
    async def static_files(name: str, request: Request):
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return Response("Not found", status_code=404, media_type="text/plain")
        st = target.stat()
        # /static/app.js?v=2.0.0 changes whenever the release does, so a versioned URL can be cached for good.
        cc = "public, max-age=31536000, immutable" if request.query_params.get("v") else "no-cache"
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if (ctype.startswith("text/") or ctype in COMPRESSIBLE) and st.st_size >= 1024:
            key = (str(target), st.st_mtime_ns, st.st_size)
            etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cc})
            memo = static_memo.get(key)
            if memo is None:
                data = await aio.run(target.read_bytes)
                memo = static_memo[key] = (data, await aio.run(_gz, data, 6))
            h = {"ETag": etag, "Cache-Control": cc, "Vary": "Accept-Encoding"}
            if "gzip" in request.headers.get("accept-encoding", ""):
                h["Content-Encoding"] = "gzip"
                return Response(memo[1], media_type=ctype, headers=h)
            return Response(memo[0], media_type=ctype, headers=h)
        return FileResponse(target, media_type=ctype, headers={"Cache-Control": cc})

    @app.get("/favicon.ico")
    async def favicon():
        return FileResponse(STATIC / "icon-32.png", media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/healthz")
    async def healthz():
        return raw_json({"ok": True, "version": __version__})

    @app.get("/manifest.webmanifest")
    async def manifest():
        return Response(orjson.dumps(dict(
            name="Ark Storage", short_name="Ark", start_url="/", display="standalone",
            background_color="#0f1115", theme_color="#0f1115",
            icons=[{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                   {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                   {"src": "/static/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}])),
            media_type="application/manifest+json")

    # ---------------------------------------------------------------- session
    @app.get("/api/session")
    async def get_session(request: Request):
        s = sess(request)
        if "csrf" not in s:
            s["csrf"] = secrets.token_hex(16)
        a = await aio.run(auth.load)
        return await jr(request, dict(authed=bool(s.get("u")), user=s.get("u"), csrf=s["csrf"],
                                      default_password=bool(a.get("default")) and bool(s.get("u")), version=__version__,
                                      theme=config.get("theme"), office_convert=office.available()))

    @app.post("/api/login")
    async def login(request: Request, d: S.Login):
        ok, wait = await auth.verify(d.username, d.password, request.client.host if request.client else "?")
        if wait:
            raise ApiError(429, f"Too many attempts. Try again in {wait} seconds.")
        if not ok:
            await asyncio.sleep(0.4)
            raise ApiError(401, "Wrong username or password")
        s = sess(request)
        s.clear()
        s.permanent = d.remember
        a = await aio.run(auth.load)
        s["u"] = a["username"]
        s["csrf"] = secrets.token_hex(16)
        return await jr(request, dict(ok=True, csrf=s["csrf"], user=s["u"], default_password=bool(a.get("default"))))

    @app.post("/api/logout")
    async def logout(request: Request):
        sess(request).clear()
        return raw_json({"ok": True})

    @router.post("/api/account")
    async def account(request: Request, d: S.Account):
        try:
            a = await auth.change(d.current, (d.username or "").strip() or None, d.password or None)
        except ValueError as e:
            raise VfsError(str(e)) from None
        sess(request)["u"] = a["username"]
        return await jr(request, dict(ok=True, user=a["username"]))

    # ---------------------------------------------------------------- browsing
    @router.get("/api/list")
    async def list_dir(request: Request, path: str = "/", hidden: str = ""):
        path = vfs.norm(path)
        i = await store.refresh()
        n = i.files.get(path)
        if not n:
            raise VfsError("Folder not found", 404)
        if not n.is_dir:
            raise VfsError("That is a file", 400)
        show = hidden == "1" or config.get("show_hidden")
        ck = (i.key, path, bool(show))
        hit = listings.get(ck)
        if hit is None:
            def build():
                kids = [k for k in i.files.ls(path) if show or not k.name.startswith(".")]
                kids.sort(key=lambda k: (not k.is_dir, k.name.lower()))
                return [entry(k, i.files.children.get(k.path, ())) for k in kids]
            entries = await cpu(build, len(i.files.children.get(path, ())))
            crumbs, acc = [{"name": "Home", "path": "/"}], ""
            for seg in [x for x in path.split("/") if x]:
                acc += "/" + seg
                crumbs.append({"name": seg, "path": acc})
            body = orjson.dumps(dict(path=path, parent=None if path == "/" else vfs.parent(path), crumbs=crumbs,
                                     entries=entries, head=i.key[:7]))
            for k in [k for k in listings if k[0] != i.key]:      # the repository moved on: forget the old renderings
                del listings[k]
            if len(listings) >= 64:
                listings.pop(next(iter(listings)))
            hit = listings[ck] = [body, None]
        h = {}
        body = hit[0]
        if len(body) > 1400 and "gzip" in request.headers.get("accept-encoding", ""):
            if hit[1] is None:
                hit[1] = _gz(body) if len(body) < 65536 else await aio.run(_gz, body)
            body = hit[1]
            h = {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
        return Response(body, headers=h, media_type="application/json")

    @router.get("/api/search")
    async def search(request: Request, q: str = "", path: str = "/"):
        q = q.strip().lower()
        base = vfs.norm(path)
        if not q:
            return await jr(request, dict(entries=[]))
        i = await store.refresh()

        def scan():
            out = []
            for n in i.files.walk(base):
                if q in n.name.lower() and not any(s.startswith(".") for s in n.path.split("/") if s):
                    out.append(entry(n, i.files.children.get(n.path, ())))
                    if len(out) >= 500:
                        break
            return out
        out = await aio.run(scan)
        return await jr(request, dict(entries=out, truncated=len(out) >= 500))

    @router.get("/api/stat")
    async def stat(request: Request, path: str = "/"):
        i = await store.refresh()
        n = node_of(i, path)
        d = entry(n, i.files.children.get(n.path, ()))
        if n.is_dir:
            def sums():
                sub = list(i.files.walk(n.path))
                return dict(files=sum(1 for s in sub if not s.is_dir), folders=sum(1 for s in sub if s.is_dir),
                            size=sum(s.size for s in sub if not s.is_dir))
            d.update(await aio.run(sums))
        else:
            d.update(stored=n.stored_size, sha=n.sha, repo_path=n.repo, pieces=len(n.parts))
        return await jr(request, d)

    @router.post("/api/exists")
    async def exists(request: Request, d: S.Paths):
        i = await store.refresh()
        return await jr(request, dict(existing=[p for p in map(vfs.norm, d.paths) if i.files.get(p)]))

    # ---------------------------------------------------------------- reading
    async def serve_file(request, n, i, download=False, ctype=None):
        reader = store.reader(n, i)
        size = n.size
        ct = ctype or mime_of(n.name)
        h = {"Accept-Ranges": "bytes", "ETag": f'"{n.sha[:16]}"', "Cache-Control": "private, max-age=0, must-revalidate"}
        quoted = n.name.replace('"', "")
        ascii_name = quoted.encode("ascii", "replace").decode().replace("?", "_")
        h["Content-Disposition"] = (f'{"attachment" if download else "inline"}; filename="{ascii_name}"; '
                                    f"filename*=UTF-8''{quote(n.name)}")
        if ext_of(n.name) in ACTIVE:
            h["Content-Security-Policy"] = "sandbox"
        if request.headers.get("if-none-match") == h["ETag"] and not request.headers.get("range"):
            return Response(status_code=304, headers=h)
        rng = parse_range(request.headers.get("range"), size) if size else None
        if rng is False:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if rng:
            a, b = rng
            h["Content-Range"] = f"bytes {a}-{b}/{size}"
            h["Content-Length"] = str(b - a + 1)
            status, lo, hi = 206, a, b + 1
        else:
            h["Content-Length"] = str(size)
            status, lo, hi = 200, 0, size
        if request.method == "HEAD":
            return Response(status_code=status, headers=h, media_type=ct)
        gen = reader.iter(lo, hi)
        # verify the first block now so a wrong passphrase is a clean error, not a broken stream
        first = await anext(gen, b"")

        async def chain():
            try:
                if first:
                    yield first
                async for chunk in gen:
                    yield chunk
            finally:
                with anyio.CancelScope(shield=True):
                    await gen.aclose()
        return StreamingResponse(chain(), status_code=status, headers=h, media_type=ct)

    @router.api_route("/api/raw", methods=["GET", "HEAD"])
    async def raw(request: Request, path: str = "", download: str = ""):
        i = await store.refresh()
        return await serve_file(request, node_of(i, path, file=True), i, download=download == "1")

    async def office_pdf(request, n, i):
        """Old Office formats (.doc, .ppt, .odt, ...) as a PDF, converted once with LibreOffice when it is installed."""
        if EXT_KIND.get(ext_of(n.name)) != "office" or ext_of(n.name) in OFFICE_CLIENT:
            raise VfsError("This file is shown directly in the browser", 400)
        pdf, data = await office.to_pdf(n, store.reader(n, i))
        if pdf:
            return FileResponse(pdf, media_type="application/pdf", headers={"Cache-Control": "private, max-age=0, must-revalidate"})
        rng = parse_range(request.headers.get("range"), len(data)) if data else None
        h = {"Cache-Control": "no-store", "Accept-Ranges": "bytes"}
        if rng:
            h["Content-Range"] = f"bytes {rng[0]}-{rng[1]}/{len(data)}"
            return Response(data[rng[0]:rng[1] + 1], status_code=206, headers=h, media_type="application/pdf")
        return Response(data, headers=h, media_type="application/pdf")

    @router.get("/api/preview-pdf")
    async def preview_pdf(request: Request, path: str = ""):
        i = await store.refresh()
        return await office_pdf(request, node_of(i, path, file=True), i)

    @router.post("/api/warm")
    async def warm(d: S.PathBody):
        """Start downloading the first pieces of a file in the background (called when the pointer rests on it)."""
        n = node_of(await store.refresh(), d.path, file=True)
        store.prefetch([p[1] for p in n.parts[:3]])
        return Response(status_code=204)

    async def zip_entries(i, nodes):
        def factory(n):
            return lambda: store.reader(n, i).iter()
        for n in nodes:
            if not n.is_dir:
                yield (n.name, n.mtime, factory(n))
                continue
            base = n.path.rstrip("/")
            yield (n.name + "/", 0, None)
            for k, d in enumerate(i.files.walk(n.path)):
                rel = n.name + d.path[len(base):]
                yield (rel + "/", 0, None) if d.is_dir else (rel, d.mtime, factory(d))
                if k % 500 == 499:
                    await asyncio.sleep(0)            # a huge folder must not hog the loop while its entries are listed

    @router.get("/api/zip")
    async def zip_download(p: list[str] = Query(default=[]), name: str = ""):
        if not p:
            raise VfsError("Nothing selected")
        i = await store.refresh()
        nodes = [node_of(i, x) for x in p]
        fname = (name or (nodes[0].name if len(nodes) == 1 else "download")) + ".zip"
        return StreamingResponse(zipstream.stream(zip_entries(i, nodes)), media_type="application/zip", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}", "Cache-Control": "no-store"})

    @router.get("/api/text")
    async def read_text(request: Request, path: str = ""):
        i = await store.refresh()
        n = node_of(i, path, file=True)
        if EXT_KIND.get(ext_of(n.name)) in ("archive", "binary", "image", "video", "audio", "pdf", "office"):
            return await jr(request, dict(error="This looks like a binary or compressed file, so it can't be edited as text.", binary=True), 415)
        if n.size > TEXT_LIMIT:
            return await jr(request, dict(error=f"File is larger than {TEXT_LIMIT // 1048576} MB; download it instead.", binary=True), 413)
        data = await store.reader(n, i).read_all(TEXT_LIMIT)
        if b"\x00" in data[:8192]:
            return await jr(request, dict(error="This file contains binary data, so it can't be edited as text.", binary=True), 415)
        return await jr(request, dict(path=n.path, content=data.decode("utf-8", "replace"), size=n.size, sha=n.sha))

    @router.put("/api/text")
    async def write_text(request: Request, d: S.TextPut):
        path = vfs.norm(d.path)
        i = await store.refresh()
        cur = i.files.get(path)
        if cur and cur.is_dir:
            raise VfsError("That is a folder")
        if cur and d.sha and d.sha != cur.sha and not d.force:
            raise VfsError("This file changed since you opened it. Reload it, or save again to overwrite.", 409)
        data = d.content.encode("utf-8")
        async with store.tx() as tx:
            await tx.put_bytes(path, data, policy="replace", enc=cur.enc if cur else None)
            await store.commit(tx, f"Edit {vfs.basename(path)}")
        n = store.idx.files.get(path)
        return await jr(request, dict(ok=True, sha=n.sha if n else "", size=len(data)))

    # ---------------------------------------------------------------- changing things
    @router.post("/api/mkdir")
    async def mkdir(request: Request, d: S.PathBody):
        p = vfs.norm(d.path)
        r = await run_tx(f"Create folder {vfs.basename(p)}", lambda tx: tx.mkdir(p))
        return await jr(request, dict(ok=True, results=r))

    @router.post("/api/newfile")
    async def newfile(request: Request, d: S.NewFile):
        p = vfs.norm(d.path)
        if (await store.refresh()).files.get(p):
            raise VfsError("A file or folder with that name already exists", 409)
        r = await run_tx(f"Create {vfs.basename(p)}", lambda tx: tx.put_bytes(p, d.content.encode(), policy="fail"))
        return await jr(request, dict(ok=True, results=r))

    @router.post("/api/rename")
    async def rename(request: Request, d: S.Rename):
        p = vfs.norm(d.path)
        name = vfs.validate_name(d.name.strip())
        n = node_of(await store.refresh(), p)
        r = await run_tx(f"Rename {n.name} to {name}", lambda tx: tx.move([p], vfs.parent(p), policy="fail", newname=name))
        return await jr(request, dict(ok=True, results=r))

    @router.post("/api/move")
    async def move(request: Request, d: S.Move):
        r = await run_tx(f"{'Copy' if d.copy_ else 'Move'} {len(d.paths)} item(s)",
                         lambda tx: tx.move(d.paths, d.dest, policy=d.on_conflict, copy=d.copy_))
        return await jr(request, dict(ok=True, results=r))

    @router.post("/api/delete")
    async def delete(request: Request, d: S.Delete):
        r = await run_tx(f"Delete {len(d.paths)} item(s)", lambda tx: tx.delete(d.paths, permanent=d.permanent))
        return await jr(request, dict(ok=True, results=r, trashed=bool(config.get("trash") and not d.permanent)))

    @router.get("/api/trash")
    async def trash_list(request: Request):
        i = await store.refresh()
        out = []
        for tid, m in i.trash_meta.items():
            if i.trash.ls("/" + tid):
                out.append({"id": tid, "name": m["name"], "path": m["path"], "dir": m["dir"], "ts": m["ts"], "size": m.get("size", 0)})
        out.sort(key=lambda x: -x["ts"])
        return await jr(request, dict(items=out))

    @router.post("/api/trash/restore")
    async def trash_restore(request: Request, d: S.Ids):
        ids = d.ids or []
        r = await run_tx(f"Restore {len(ids)} item(s)", lambda tx: tx.restore(ids))
        return await jr(request, dict(ok=True, results=r))

    @router.post("/api/trash/empty")
    async def trash_empty(request: Request, d: S.Ids | None = None):
        ids = d.ids if d else None
        await run_tx("Empty trash" if not ids else "Delete from trash", lambda tx: tx.empty_trash(ids))
        return await jr(request, dict(ok=True))

    # ---------------------------------------------------------------- uploads
    # Chunks may arrive in parallel and out of order. They are ingested strictly in order; every finished
    # piece is hashed straight into git and pushed in rolling batches, so disk use stays flat. The request body is
    # streamed from the socket to a temp file (never held whole in memory) while other requests keep being served.
    def batch_dir(bid):
        if not re.fullmatch(r"[0-9a-f]{16}", bid or ""):
            raise VfsError("Bad upload id", 400)
        return config.DATA_DIR / "uploads" / bid

    async def end_batch(bid, u):
        await u["ing"].abort()
        await u["ing"].close()
        await aio.run(shutil.rmtree, batch_dir(bid), ignore_errors=True)

    def get_batch(bid):
        u = uploads.get(bid)
        if u is None:
            raise VfsError("Unknown upload", 404)
        return u

    def tidy_dirs(root, live, now):
        for old in root.iterdir():                       # abandoned directories (e.g. after a restart)
            if old.name not in live and now - old.stat().st_mtime > 3600:
                shutil.rmtree(old, ignore_errors=True)
        for old in config.DATA_DIR.glob("ark-obj-*"):
            if now - old.stat().st_mtime > 6 * 3600:
                shutil.rmtree(old, ignore_errors=True)

    def has_staging(i):
        return any(p.startswith(STAGE) for p in i.entries)

    @router.post("/api/upload/begin")
    async def upload_begin(request: Request, d: S.UploadBegin):
        limit = config.get("max_upload_mb") * 1048576
        total = 0
        for f in d.files:
            if limit and f.size > limit:
                raise VfsError(f"'file' is larger than the upload limit ({config.get('max_upload_mb')} MB). Raise it in Settings.", 413)
            total += f.size
        root = config.DATA_DIR / "uploads"
        root.mkdir(parents=True, exist_ok=True)
        now = time.time()
        stale = [(k, uploads.pop(k)) for k in [k for k, u in uploads.items() if now - u["t"] > 6 * 3600]]
        live = set(uploads)
        for k, u in stale:
            await end_batch(k, u)
        await aio.run(tidy_dirs, root, live, now)
        if not live:
            with contextlib.suppress(Exception):         # several worker processes: wait until a batch is seen idle twice
                if await aio.run(has_staging, await store.refresh()):
                    await store.sweep_staging(live, confirm=int(os.environ.get("ARK_WORKERS", "1") or 1) > 1)
        chunk = config.get("chunk_mb") * 1048576
        need = min(total, config.get("stage_mb") * 1048576 + 3 * chunk) + 4 * UP_CHUNK + 64 * 1048576
        free = shutil.disk_usage(root).free
        if need > free:
            raise VfsError(f"Not enough free disk space on the server for this upload: it needs about "
                           f"{need // 1048576} MB free (it streams to GitHub, so this does not grow with the file). "
                           f"Lower 'Push batch' in Settings -> Performance to need less.", 507)
        bid = secrets.token_hex(8)
        ing = await Ingest.create(store, bid)
        (root / bid).mkdir()
        fs = {f.id: {"size": f.size, "got": 0, "st": None, "pend": {}, "parts": None, "err": "", "lock": asyncio.Lock()}
              for f in d.files}
        uploads[bid] = {"ing": ing, "files": fs, "t": now}
        return await jr(request, dict(batch=bid, chunk=UP_CHUNK))

    def drain(F):
        """Feed every contiguous received chunk into the stager (blocking: hashing, AES, git; runs in a worker thread)."""
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

    @router.put("/api/upload/{bid}/{fid}")
    async def upload_chunk(bid: str, fid: str, request: Request, offset: int = 0):
        u = get_batch(bid)
        F = u["files"].get(fid)
        d = batch_dir(bid)
        if F is None or not d.exists():
            raise VfsError("Unknown upload", 404)
        safe = re.sub(r"\W", "_", fid)
        tmp = d / f"{safe}.{offset}"
        n = 0
        try:
            async with await anyio.open_file(tmp, "wb") as f:
                buf = bytearray()
                async for blk in request.stream():
                    n += len(blk)
                    if offset + n > F["size"]:
                        raise VfsError("Received more data than announced", 400)
                    buf += blk
                    if len(buf) >= 1 << 20:
                        await f.write(buf)
                        buf.clear()
                if buf:
                    await f.write(buf)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            raise
        async with F["lock"]:
            if F["err"]:
                os.remove(tmp)
                raise VfsError(F["err"], 500)
            if F["parts"] is not None or offset < F["got"]:          # a retry of data we already hold
                with contextlib.suppress(OSError):
                    os.remove(tmp)
                return await jr(request, dict(ok=True, got=F["got"]))
            F["pend"][offset] = (tmp, n)
            try:
                if F["st"] is None:
                    F["st"] = u["ing"].stager(safe)
                await aio.run(drain, F)
            except Exception as e:                                  # noqa: BLE001
                F["err"] = f"Could not process the upload: {e}"
                raise VfsError(F["err"], 500) from None
            got = F["got"]
        await u["ing"].maybe_flush()
        return await jr(request, dict(ok=True, got=got))

    @router.post("/api/upload/{bid}/commit")
    async def upload_commit(request: Request, bid: str, d: S.UploadCommit):
        u = get_batch(bid)
        ing = u["ing"]
        plan = []
        for f in d.files:
            F = u["files"].get(f.id)
            if F is None or F["parts"] is None:
                raise VfsError(f"'{f.path}' was not fully received", 400)
            path = vfs.norm(f.path)
            vfs.validate_name(vfs.basename(path))
            plan.append((path, F["parts"], f.mtime, F["size"]))
        empty_dirs = [vfs.norm(p) for p in d.dirs]
        title = f"Upload {len(plan)} file(s)" if len(plan) != 1 else f"Upload {vfs.basename(plan[0][0])}"

        async def run(job):
            ok = False
            try:
                async with store.tx(ingest=ing) as tx:
                    for path, parts, mtime, size in plan:
                        tx.put_staged(path, parts, ing.enc, mtime=(mtime / 1000 if mtime else None), policy=d.on_conflict, expect=size)
                    for dd in empty_dirs:
                        tx.mkdir(dd)
                    job.update(0.85, "Pushing to GitHub")
                    res = await store.commit(tx, title)
                    ing.staged.clear()
                    ok = True
                    return res
            finally:
                with anyio.CancelScope(shield=True):
                    if not ok:
                        await ing.abort()
                    await ing.close()
                    await aio.run(shutil.rmtree, batch_dir(bid), ignore_errors=True)
                    uploads.pop(bid, None)
        return await jr(request, dict(job=jobs.start("upload", title, run).to_dict()))

    @router.post("/api/upload/{bid}/cancel")
    async def upload_cancel(request: Request, bid: str):
        u = uploads.pop(bid, None)
        if u:
            await end_batch(bid, u)
        return await jr(request, dict(ok=True))

    # ---------------------------------------------------------------- compress / extract
    @router.post("/api/compress")
    async def compress(request: Request, d: S.Compress):
        dest = vfs.norm(d.dest)
        name = vfs.validate_name(str(d.name or "archive.zip"))
        i = await store.refresh()
        nodes = [node_of(i, p) for p in d.paths]

        async def run(job):
            fd, tmpname = tempfile.mkstemp(dir=config.DATA_DIR, prefix="zip-")
            os.close(fd)
            try:
                async with await anyio.open_file(tmpname, "wb") as f:
                    k = 0
                    async for chunk in zipstream.stream(zip_entries(i, nodes)):
                        await f.write(chunk)
                        k += 1
                        job.update(min(0.7, k / 4000), "Compressing")
                async with store.tx() as tx:
                    job.update(0.75, "Storing archive")
                    await tx.put(vfs.join(dest, name), tmpname, policy=d.on_conflict)
                    return await store.commit(tx, f"Compress into {name}")
            finally:
                with contextlib.suppress(OSError):
                    os.remove(tmpname)
        return await jr(request, dict(job=jobs.start("compress", f"Compress to {name}", run).to_dict()))

    @router.post("/api/extract")
    async def extract(request: Request, d: S.Extract):
        i = await store.refresh()
        n = node_of(i, d.path, file=True)
        if ext_of(n.name) != "zip":
            raise VfsError("Only .zip archives can be extracted here")
        stem = n.name[:-4] or "extracted"
        dest = vfs.norm(d.dest or vfs.join(vfs.parent(n.path), stem))
        limit = max(1, config.get("max_upload_mb")) * 1048576

        async def run(job):
            fd, tmpname = tempfile.mkstemp(dir=config.DATA_DIR, prefix="unzip-")
            os.close(fd)
            skipped = 0
            try:
                async with await anyio.open_file(tmpname, "wb") as f:
                    async for chunk in store.reader(n, i).iter():
                        await f.write(chunk)
                enc = store.encrypt_enabled()
                master = await store.master_for_write() if enc else None
                async with store.tx() as tx:
                    def work():
                        nonlocal skipped
                        with zipfile.ZipFile(tmpname) as z:
                            infos = z.infolist()
                            if len(infos) > 20000 or sum(x.file_size for x in infos) > limit:
                                raise VfsError("Archive is too large to extract here")
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
                                tx.put_blocking(target, out, mtime=time.mktime(info.date_time + (0, 0, -1)), policy=d.on_conflict,
                                                enc=enc, master=master)
                                os.remove(out)
                    await aio.run(work)
                    job.update(0.9, "Pushing to GitHub")
                    await store.commit(tx, f"Extract {n.name}")
                return {"dest": dest, "skipped": skipped}
            finally:
                with contextlib.suppress(OSError):
                    os.remove(tmpname)
        return await jr(request, dict(job=jobs.start("extract", f"Extract {n.name}", run).to_dict()))

    # ---------------------------------------------------------------- sharing
    # One link, two behaviours. A browser (it asks for text/html) gets a preview page with Download / Raw / Copy buttons;
    # anything else (curl, wget, fetch, Invoke-WebRequest...) gets the file's bytes, so ``curl -fsSL <link> | sh`` works.
    # ?raw=1 forces the bytes, ?download=1 forces a download, ?preview=1 forces the page. The file name after the token is
    # decoration (it makes ``curl -O`` and the address bar look right); the token alone identifies the file.
    TOOL_UA = re.compile(r"^(curl|wget|fetch|httpie|python-requests|python-urllib|go-http|libwww|powershell|windowspowershell|"
                         r"aria2|axios|node-fetch|okhttp|java|ruby|php|perl)", re.I)
    RUNNERS = {"sh": "sh", "bash": "bash", "zsh": "zsh", "ksh": "ksh", "py": "python3 -", "js": "node", "mjs": "node",
               "rb": "ruby", "pl": "perl", "php": "php", "lua": "lua"}

    def base_url(request):
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        return f"{scheme}://{host}"

    def share_path(token, name):
        return f"/s/{token}/{quote(name, safe='')}"

    def share_commands(url, name):
        """Ready-to-paste shell commands for the share page: run it (scripts only) and save it."""
        out = []
        ext = ext_of(name)
        if ext in RUNNERS:
            out.append(("Run", f"curl -fsSL {url} | {RUNNERS[ext]}"))
        elif ext == "ps1":
            out.append(("Run (PowerShell)", f"irm {url} | iex"))
        out.append(("Save", f"curl -fLO {url}"))
        return out

    def wants_page(request, q):
        if q.get("raw") or q.get("download") or q.get("pdf"):
            return False
        if q.get("preview") == "1":
            return True
        if TOOL_UA.match(request.headers.get("user-agent", "")):
            return False
        return "text/html" in request.headers.get("accept", "")

    def share_page(request, token, n):
        name, kind, ext = n.name, kind_of(n.name), ext_of(n.name)
        url = base_url(request) + share_path(token, name)
        e = htmllib.escape
        cmds = "".join(f'<div class="cmd"><span class="cl">{e(label)}</span><code>{e(c)}</code>'
                       f'<button class="btn" type="button" data-copy="{e(c)}">Copy</button></div>' for label, c in share_commands(url, name))
        desc = f"{name} - {_human(n.size)} - shared from Ark Storage"
        doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{e(name)} - Ark Storage</title>
<meta name="robots" content="noindex, nofollow"><meta name="theme-color" content="#0f1115">
<meta name="ark-version" content="{__version__}">
<meta property="og:title" content="{e(name)}"><meta property="og:description" content="{e(desc)}"><meta property="og:type" content="website">
<link rel="icon" href="/static/icon-32.png?v={__version__}" type="image/png" sizes="32x32">
<link rel="stylesheet" href="/static/style.css?v={__version__}"><link rel="stylesheet" href="/static/share.css?v={__version__}">
</head><body class="share">
<header class="sh-head">
  <img src="/static/icon-192.png?v={__version__}" alt="" width="36" height="36">
  <div class="sh-title"><h1 title="{e(name)}">{e(name)}</h1><p class="muted">{e(_human(n.size))} &middot; {e(mime_of(name))}</p></div>
  <div class="sh-actions">
    <a class="btn" id="sh-raw" href="{e(share_path(token, name))}?raw=1" target="_blank" rel="noopener">Raw</a>
    <button class="btn" type="button" id="sh-link" data-copy="{e(url)}">Copy link</button>
    <a class="btn primary" id="sh-dl" href="{e(share_path(token, name))}?download=1">Download</a>
  </div>
</header>
<section class="sh-cmds" aria-label="Command line">{cmds}</section>
<main id="sh-view" class="sh-view" data-kind="{e(kind)}" data-ext="{e(ext)}" data-name="{e(name)}" data-size="{n.size}"
      data-raw="{e(share_path(token, name))}?raw=1" data-convert="{1 if office.available() else 0}"><div class="sh-loading"><span class="vspin"></span>Loading preview&hellip;</div></main>
<noscript><p class="sh-note">Preview needs JavaScript. Use Download or Raw above.</p></noscript>
<script src="/static/hljs/highlight.bundle.js?v={__version__}"></script>
<script src="/static/pdfview.js?v={__version__}"></script>
<script src="/static/office.js?v={__version__}"></script>
<script src="/static/share.js?v={__version__}"></script>
</body></html>"""
        return Response(doc, media_type="text/html; charset=utf-8", headers={
            "Cache-Control": "private, no-cache", "Content-Security-Policy": CSP})

    @router.post("/api/share")
    async def share(request: Request, d: S.Share):
        n = node_of(await store.refresh(), d.path, file=True)
        hours = int(d.hours or config.get("share_hours"))
        token = shares.dumps({"p": n.path, "e": int(time.time() + hours * 3600)})
        return await jr(request, dict(url=base_url(request) + share_path(token, n.name), hours=hours))

    @app.api_route("/s/{token}", methods=["GET", "HEAD"])
    @app.api_route("/s/{token}/{name:path}", methods=["GET", "HEAD"])
    async def shared(request: Request, token: str, name: str = ""):
        q = request.query_params
        try:
            d = shares.loads(token)
        except BadSignature:
            return Response("Invalid link.", status_code=404, media_type="text/plain")
        if d["e"] < time.time():
            return Response("This link has expired.", status_code=410, media_type="text/plain")
        try:
            i = await store.refresh()
            n = i.files.get(d["p"])
            if not n or n.is_dir:
                return Response("This file no longer exists.", status_code=404, media_type="text/plain")
            if q.get("pdf"):
                return await office_pdf(request, n, i)
            if wants_page(request, q):
                return share_page(request, token, n)
            download = bool(q.get("download")) or q.get("inline") == "0"
            # Scripts and source files are served as text/plain so a browser (or curl -i) shows them instead of saving them.
            ctype = "text/plain; charset=utf-8" if not download and kind_of(n.name) == "text" else None
            return await serve_file(request, n, i, download=download, ctype=ctype)
        except VfsError as e:
            return Response(str(e), status_code=e.status, media_type="text/plain")

    # ---------------------------------------------------------------- jobs, status, settings
    @router.get("/api/jobs")
    async def jobs_list(request: Request):
        return await jr(request, dict(jobs=jobs.list()))

    @router.get("/api/jobs/{jid}")
    async def job_get(request: Request, jid: str):
        j = jobs.get(jid)
        if not j:
            raise VfsError("Unknown job", 404)
        return await jr(request, j.to_dict())

    @router.get("/api/status")
    async def status(request: Request):
        out = {"version": __version__, "problems": store.problems(), "branch": config.get("branch"),
               "repo_url": config.get("repo_url"), "git": bool(shutil.which("git")), "crypto": store.crypto_state(),
               "cache_bytes": await aio.run(store.cache.size), "last_error": store.last_error, "trash": bool(config.get("trash")),
               "office_convert": office.available()}
        if not out["problems"]:
            try:
                info = await repo_info()
                await store.refresh()
                out.update(repo=info["full_name"], private=info["private"], repo_kb=info.get("size", 0),
                           can_push=bool((info.get("permissions") or {}).get("push", True)),
                           stats=await aio.run(store.stats), crypto=store.crypto_state())
            except (VfsError, GitHubError) as e:
                out["error"] = str(e)
        return await jr(request, out)

    @router.get("/api/settings")
    async def settings_get(request: Request):
        return await jr(request, dict(settings=config.public_view(), problems=store.problems()))

    @router.put("/api/settings")
    async def settings_put(request: Request, d: S.SettingsPut):
        changes = d.changes
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
                import base64

                def matches():
                    key = crypto.derive_master(phrase, base64.b64decode(conf["salt"]))
                    try:
                        return crypto.decrypt_bytes(key, base64.b64decode(conf["check"])) == b"ark-storage"
                    except crypto.CryptoError:
                        return False
                if not await aio.run(matches):
                    raise VfsError("That passphrase does not match the one this repository was encrypted with.")
        try:
            await aio.run(config.update, changes)
        except ValueError as e:
            raise VfsError(str(e)) from None
        if {"repo_url", "token", "branch", "passphrase"} & set(changes):
            store.reset()
            info_cache["t"] = 0
        return await jr(request, dict(ok=True, settings=config.public_view(), problems=store.problems()))

    @router.post("/api/settings/test")
    async def settings_test(request: Request):
        store.reset()
        info_cache["t"] = 0
        i = await store.refresh(force=True)
        info = await repo_info()
        return await jr(request, dict(ok=True, repo=info["full_name"], private=info["private"], head=i.key[:7],
                                      can_push=bool((info.get("permissions") or {}).get("push", True)),
                                      message="Connected. The storage branch is ready."))

    @router.post("/api/admin/{action}")
    async def admin(request: Request, action: str, d: S.Admin | None = None):
        d = d or S.Admin()
        if action == "clear-cache":
            await aio.run(store.cache.clear)
            return await jr(request, dict(ok=True))
        if action == "refresh":
            store.client._etags.clear()
            await store.refresh(force=True)
            return await jr(request, dict(ok=True))
        if action == "cleanup":
            removed = await store.sweep_staging(set(uploads))
            return await jr(request, dict(ok=True, removed=removed, incomplete=len((await store.refresh()).files.incomplete)))
        if action == "squash":
            async def squash(_job):
                return await store.squash()
            return await jr(request, dict(job=jobs.start("squash", "Compact history", squash).to_dict()))
        if action == "convert":
            to = d.encrypted
            if to and not config.get("passphrase"):
                raise VfsError("Set a passphrase first (Settings -> Encryption).")

            async def convert(job):
                if to:
                    await store.master_for_write()
                return await store.convert_all(to, lambda a, b, p: job.update(a / b, f"{a}/{b}  {p}"))
            title = "Encrypt all files" if to else "Decrypt all files"
            return await jr(request, dict(job=jobs.start("convert", title, convert).to_dict()))
        if action == "reset-crypto":
            i = await store.refresh()
            if any(n.enc for n in i.files.nodes.values() if not n.is_dir):
                raise VfsError("Some files are still encrypted. Decrypt them all first.")
            await run_tx("Reset encryption setup", lambda tx: tx.set_conf({}))
            store._master = None
            return await jr(request, dict(ok=True))
        raise VfsError("Unknown action", 404)

    app.include_router(router)
    return app
