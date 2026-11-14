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

    def repo(self):
        return {"full_name": "test/repo", "private": True, "size": 1, "permissions": {"push": True}}

    def tree(self, branch):
        p = self._git("rev-parse", "--verify", "-q", f"refs/heads/{branch}^{{tree}}")
        if p.returncode:
            return None
        key = p.stdout.decode().strip()
        out = self._git("ls-tree", "-r", "-t", "-l", "-z", f"refs/heads/{branch}").stdout.decode()
        entries = []
        for rec in filter(None, out.split("\0")):
            meta, path = rec.split("\t", 1)
            mode, typ, sha, size = meta.split()
            entries.append({"path": path, "type": typ, "sha": sha, "mode": mode, "size": 0 if size == "-" else int(size)})
        return key, entries, True

    def download_blob(self, sha, dest, progress=None):
        with open(dest, "wb") as f:
            f.write(self._git("cat-file", "blob", sha).stdout)

    def delete_branch(self, branch):
        self._git("update-ref", "-d", f"refs/heads/{branch}")


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
