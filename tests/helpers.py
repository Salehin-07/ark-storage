"""Test doubles: a local bare git repository stands in for GitHub (API client + remote)."""
import contextlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from arkstorage import config
from arkstorage.gh import Client, Git
from arkstorage.store import Store


def sh(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, **kw)


class FakeClient(Client):
    def __init__(self, remote):
        super().__init__()
        self.remote = remote

    def _git(self, *a):
        return subprocess.run(["git", "--git-dir", self.remote, *a], capture_output=True)

    async def repo(self):
        return {"full_name": "test/repo", "private": True, "size": 1, "permissions": {"push": True}}

    async def tree(self, branch):
        p = await self._agit("rev-parse", "--verify", "-q", f"refs/heads/{branch}^{{tree}}")
        if p.returncode:
            return None
        key = p.stdout.decode().strip()
        out = (await self._agit("ls-tree", "-r", "-t", "-l", "-z", f"refs/heads/{branch}")).stdout.decode()
        entries = []
        for rec in filter(None, out.split("\0")):
            meta, path = rec.split("\t", 1)
            mode, typ, sha, size = meta.split()
            entries.append({"path": path, "type": typ, "sha": sha, "mode": mode, "size": 0 if size == "-" else int(size)})
        return key, entries, True

    async def download_blob(self, sha, dest, progress=None):
        tmp = f"{dest}.part"
        data = (await self._agit("cat-file", "blob", sha)).stdout
        with open(tmp, "wb") as f:
            f.write(data)
        import os
        os.replace(tmp, dest)

    async def delete_branch(self, branch):
        await self._agit("update-ref", "-d", f"refs/heads/{branch}")

    async def _agit(self, *a):
        from arkstorage import aio
        return await aio.run(self._git, *a)


class FakeGit(Git):
    def __init__(self, remote):
        super().__init__()
        self.remote = remote

    def url(self):
        return self.remote

    def _auth(self):
        return ["-c", "gc.auto=0"]


@contextlib.contextmanager
def sandbox(**settings):
    """A temp data dir + empty bare remote + Store wired to them. Yields (store, remote_path)."""
    tmp = Path(tempfile.mkdtemp(prefix="ark-test-"))
    old = config.DATA_DIR
    config.DATA_DIR = tmp / "data"
    config.DATA_DIR.mkdir()
    config._saved.clear()
    from arkstorage import auth
    auth._fails.clear()
    remote = str(tmp / "remote.git")
    sh("git", "init", "--bare", "-q", remote)
    # behave like GitHub: serve partial-clone fetches and lazy blob fetches
    sh("git", "--git-dir", remote, "config", "uploadpack.allowFilter", "true")
    sh("git", "--git-dir", remote, "config", "uploadpack.allowAnySHA1InWant", "true")
    base = {"repo_url": "test/repo", "token": "t", "refresh_seconds": 0, "chunk_mb": 1}
    base.update(settings)
    config.update(base)
    store = Store()
    store.client = FakeClient(remote)
    store.cache.client = store.client
    store.git = FakeGit(remote)
    store.cache.dir = config.DATA_DIR / "cache"
    try:
        yield store, remote
    finally:
        config.DATA_DIR = old
        config._saved.clear()
        config.load()
        shutil.rmtree(tmp, ignore_errors=True)


import http.client  # noqa: E402,F401
import unittest  # noqa: E402


class R:
    """Response wrapper keeping the small vocabulary the tests use (get_json, data, ...)."""

    def __init__(self, r):
        self._r = r

    def __getattr__(self, k):
        return getattr(self._r, k)

    def get_json(self):
        return self._r.json()

    @property
    def data(self):
        return self._r.content

    def get_data(self, as_text=False):
        return self._r.text if as_text else self._r.content


class C:
    def __init__(self, tc):
        self.tc = tc

    def _do(self, method, url, **kw):
        if "data" in kw:
            kw["content"] = kw.pop("data")
        return R(getattr(self.tc, method)(url, **kw))

    def get(self, url, **kw):
        return self._do("get", url, **kw)

    def post(self, url, **kw):
        return self._do("post", url, **kw)

    def put(self, url, **kw):
        return self._do("put", url, **kw)


class ApiCase(unittest.TestCase):
    """Closes every TestClient (and with it the app's event loop and lifespan) a test opened."""

    def setUp(self):
        from tests import api_harness
        api_harness.LIVE.clear()

    def tearDown(self):
        from tests import api_harness
        for tc in api_harness.LIVE:
            tc.__exit__(None, None, None)
        api_harness.LIVE.clear()


def run(coro):
    """Drive a coroutine of a stand-alone Store from synchronous test code."""
    import asyncio
    return asyncio.run(coro)
