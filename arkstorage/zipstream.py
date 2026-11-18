"""Stream a zip archive without building it in memory or on disk (async).

Deflate is CPU work, so every chunk is compressed in a worker thread; the event loop only moves bytes.
"""
import time
import zipfile

from . import aio


class _Sink:
    def __init__(self):
        self.buf = []
        self.pos = 0

    def write(self, b):
        self.buf.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def flush(self):
        pass

    def drain(self):
        out = b"".join(self.buf)
        self.buf = []
        return out


async def stream(entries, level=1):
    """entries: async iterable of (arcname, mtime, factory); factory() returns an async iterator of bytes
    (or None for a folder). Yields zip bytes."""
    sink = _Sink()
    zf = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True)
    async for arcname, mtime, factory in entries:
        if arcname.endswith("/"):
            zi = zipfile.ZipInfo(arcname, time.gmtime(mtime or time.time())[:6])
            zi.external_attr = 0o40755 << 16
            zf.writestr(zi, b"")
        else:
            zi = zipfile.ZipInfo(arcname, time.gmtime(max(mtime or time.time(), 315532800))[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            dest = zf.open(zi, "w", force_zip64=True)
            try:
                src = factory()
                try:
                    async for chunk in src:
                        await aio.run(dest.write, chunk)
                        data = sink.drain()
                        if data:
                            yield data
                finally:
                    await src.aclose()
            finally:
                dest.close()
        data = sink.drain()
        if data:
            yield data
    zf.close()
    data = sink.drain()
    if data:
        yield data
