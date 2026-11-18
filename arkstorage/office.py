"""Optional server-side conversion of Office files the browser can't draw itself (.doc, .ppt, .odt, ...) to PDF.

The browser renders .docx/.xlsx/.pptx and friends on its own (static/office.js). Older binary formats have no good
in-browser renderer, so when LibreOffice is installed on the host they are converted to PDF once and shown with the
PDF viewer. Without LibreOffice the viewer simply offers a download.
"""
import asyncio
import contextlib
import hashlib
import os
import shutil
import tempfile
import weakref
from pathlib import Path

import anyio

from . import aio, config
from .vfs import VfsError

MAX_INPUT = 64 * 1024 * 1024          # refuse to convert bigger files (the converter holds them in memory)
KEEP_BYTES = 256 * 1024 * 1024        # converted PDFs kept on disk (plain files only), oldest removed first
TIMEOUT = 120
_locks = weakref.WeakKeyDictionary()  # event loop -> lock: one LibreOffice at a time, it shares a profile directory


def _get_lock():
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def binary():
    return shutil.which("soffice") or shutil.which("libreoffice")


def available():
    return bool(binary())


def _dir():
    d = config.DATA_DIR / "office"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trim(keep):
    files = []
    for f in _dir().glob("*.pdf"):
        with contextlib.suppress(OSError):
            st = f.stat()
            files.append((st.st_mtime, st.st_size, f))
    total = sum(x[1] for x in files)
    for _mt, sz, f in sorted(files):
        if total <= KEEP_BYTES:
            break
        if f != keep:
            with contextlib.suppress(OSError):
                f.unlink()
                total -= sz


async def to_pdf(node, reader):
    """Return (path_or_None, bytes_or_None). Plain files are cached on disk by content id; encrypted files are
    converted in a temp dir and returned from memory so no readable copy is left behind.

    LibreOffice runs as an asyncio subprocess (no worker thread parked on it); only one runs at a time."""
    exe = binary()
    if not exe:
        raise VfsError("Previewing this format needs LibreOffice on the server. Download the file instead.", 501)
    if node.size > MAX_INPUT:
        raise VfsError(f"Files over {MAX_INPUT >> 20} MB can't be previewed; download it instead.", 413)
    ext = node.name.rsplit(".", 1)[-1].lower() if "." in node.name else "bin"
    key = hashlib.sha256(f"{node.sha}:{ext}".encode()).hexdigest()[:40]
    cached = _dir() / f"{key}.pdf"
    if not node.enc and cached.exists():
        with contextlib.suppress(OSError):
            os.utime(cached)
        return cached, None
    async with _get_lock():
        if not node.enc and cached.exists():
            return cached, None
        tmp = Path(tempfile.mkdtemp(prefix="ark-office-"))
        try:
            src = tmp / f"in.{ext}"
            async with await anyio.open_file(src, "wb") as f:
                async for chunk in reader.iter(0, node.size):
                    await f.write(chunk)
            out = tmp / "out"
            out.mkdir()
            cmd = [exe, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
                   f"-env:UserInstallation={(tmp / 'profile').as_uri()}", "--convert-to", "pdf", "--outdir", str(out), str(src)]
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=tmp, env=dict(os.environ, HOME=str(tmp)),
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                await asyncio.wait_for(proc.wait(), TIMEOUT)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
                raise VfsError("Converting this file took too long; download it instead.", 504) from None
            pdf = out / "in.pdf"
            if not pdf.exists() or pdf.stat().st_size == 0:
                raise VfsError("This file could not be converted for preview; download it instead.", 422)
            if node.enc:
                return None, await aio.run(pdf.read_bytes)
            tmp_final = cached.with_suffix(".part")
            await aio.run(shutil.move, str(pdf), tmp_final)
            await aio.run(os.replace, tmp_final, cached)
            await aio.run(_trim, cached)
            return cached, None
        finally:
            await aio.run(shutil.rmtree, tmp, ignore_errors=True)
