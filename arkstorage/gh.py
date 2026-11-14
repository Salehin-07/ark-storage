"""Tiny GitHub REST client (read side) and git wrapper (write side).

Reads (ref, recursive tree with sizes, raw blobs) use the REST API: one conditional request (ETag, free
when nothing changed) refreshes the whole listing. Writes use plain `git` so that a folder of 500 files
is ONE push, not 500 API calls (GitHub rate-limits content creation to roughly 500 requests an hour).
"""
import base64
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import time

import requests

from . import config

API = "https://api.github.com"
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
NULL_SHA = "0" * 40


# Keep git's memory flat however big the upload is: no delta search, big blobs are streamed instead of
# loaded, and (important) a small HTTP post buffer so a push is streamed in chunks instead of being
# buffered whole in RAM. Level-1 compression is cheap and still shrinks text.
# pack.useSparse=false matters: the sparse walk would not recognise blobs that merely moved path (staging ->
# final) as already on the server, so git would lazily re-download what we just freed and push it again.
# (core.bigFileThreshold is set only for network commands: for `hash-object` it would write packs
# instead of loose objects, which cannot be freed one by one.)
LEAN = ["-c", "gc.auto=0", "-c", "http.postBuffer=1048576", "-c", "pack.window=0", "-c", "pack.threads=1", "-c", "pack.useSparse=false",
        "-c", "pack.compression=1", "-c", "core.looseCompression=1",
        "-c", "pack.deltaCacheSize=1m", "-c", "pack.windowMemory=8m", "-c", "core.packedGitLimit=64m",
        "-c", "core.packedGitWindowSize=16m", "-c", "core.fsync=none"]


class GitHubError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NonFastForward(GitHubError):
    pass


def scrub(text):
    text = str(text)
    tok = config.get("token")
    if tok:
        text = text.replace(tok, "***")
        text = text.replace(base64.b64encode(f"x-access-token:{tok}".encode()).decode(), "***")
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", text)


class Client:
    def __init__(self):
        self.s = requests.Session()
        self._etags = {}

    @property
    def owner_repo(self):
        url = config.get("repo_url")
        if not url:
            raise GitHubError("No repository configured. Set ARK_REPO_URL (or Settings -> Repository).")
        return config.parse_repo(url)

    def _headers(self, extra=None):
        tok = config.get("token")
        if not tok:
            raise GitHubError("No GitHub token configured. Set ARK_GITHUB_TOKEN (or Settings -> Repository).")
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ark-storage"}
        h.update(extra or {})
        return h

    def request(self, method, path, retries=3, **kw):
        extra = kw.pop("headers", None)
        url = path if path.startswith("http") else API + path
        last = None
        for attempt in range(retries):
            try:
                r = self.s.request(method, url, headers=self._headers(extra), timeout=kw.pop("timeout", 60), **kw)
            except requests.RequestException as e:
                last = GitHubError(f"could not reach GitHub: {scrub(e)}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (502, 503, 504) or (r.status_code in (403, 429) and r.headers.get("retry-after")):
                time.sleep(min(30, float(r.headers.get("retry-after", 2 * (attempt + 1)))))
                last = GitHubError(f"GitHub is busy ({r.status_code})", r.status_code)
                continue
            return r
        raise last or GitHubError("GitHub request failed")

    def _json(self, r, what):
        if r.status_code == 401:
            raise GitHubError("GitHub rejected the token (401). Check ARK_GITHUB_TOKEN.", 401)
        if r.status_code == 403:
            msg = (r.json().get("message", "") if r.content else "")
            raise GitHubError(f"GitHub refused ({what}): {msg or 'forbidden; the token needs repo access'}", 403)
        if r.status_code >= 400:
            msg = ""
            with contextlib.suppress(ValueError):
                msg = r.json().get("message", "")
            raise GitHubError(f"GitHub {what} failed ({r.status_code}): {msg}", r.status_code)
        return r.json()

    def repo(self):
        o, n = self.owner_repo
        return self._json(self.request("GET", f"/repos/{o}/{n}"), "repository lookup")

    def tree(self, branch):
        """Recursive listing of `branch` -> (commit/tree key, entries) or None if the branch is missing.

        Returns (key, entries, changed). Uses ETag so an unchanged branch costs no rate limit.
        """
        o, n = self.owner_repo
        path = f"/repos/{o}/{n}/git/trees/{branch}?recursive=1"
        hdr = {}
        cached = self._etags.get(path)
        if cached:
            hdr["If-None-Match"] = cached[0]
        r = self.request("GET", path, headers=hdr)
        if r.status_code == 304 and cached:
            return cached[1], cached[2], False
        if r.status_code in (404, 409):      # no such branch / empty repository
            self._etags.pop(path, None)
            return None
        data = self._json(r, "listing")
        entries = data["tree"]
        if data.get("truncated"):
            entries = self._walk(data["sha"])
        key = data["sha"]
        self._etags[path] = (r.headers.get("ETag", ""), key, entries)
        return key, entries, True

    def _walk(self, sha, prefix=""):
        o, n = self.owner_repo
        data = self._json(self.request("GET", f"/repos/{o}/{n}/git/trees/{sha}"), "listing")
        out = []
        for e in data["tree"]:
            e = dict(e, path=prefix + e["path"])
            out.append(e)
            if e["type"] == "tree":
                out.extend(self._walk(e["sha"], e["path"] + "/"))
        return out

    def head(self, branch):
        o, n = self.owner_repo
        r = self.request("GET", f"/repos/{o}/{n}/git/ref/heads/{branch}")
        if r.status_code in (404, 409):
            return None
        return self._json(r, "branch lookup")["object"]["sha"]

    def download_blob(self, sha, dest, progress=None):
        """Stream a raw blob (<= 100 MB) to `dest`."""
        o, n = self.owner_repo
        r = self.request("GET", f"/repos/{o}/{n}/git/blobs/{sha}", stream=True, timeout=300,
                         headers={"Accept": "application/vnd.github.raw+json"})
        if r.status_code >= 400:
            self._json(r, "download")
        tmp = f"{dest}.part{os.getpid()}"
        try:
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            os.replace(tmp, dest)
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)

    def delete_branch(self, branch):
        o, n = self.owner_repo
        r = self.request("DELETE", f"/repos/{o}/{n}/git/refs/heads/{branch}")
        if r.status_code not in (204, 404, 422):
            self._json(r, "delete branch")


class Git:
    """Local scratch clone (trees only, no file data) used to build and push commits."""

    def __init__(self):
        self.dir = config.DATA_DIR / "repo.git"
        self.last_fetch_ok = False

    def _env(self, extra=None):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
        env.pop("GIT_DIR", None)
        env.pop("GIT_INDEX_FILE", None)
        name, email = config.get("commit_name"), config.get("commit_email")
        env.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
        env.update(extra or {})
        return env

    def _auth(self):
        tok = config.get("token")
        b = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        return ["-c", f"http.extraheader=Authorization: Basic {b}", "-c", "protocol.version=2"]

    def url(self):
        o, n = config.parse_repo(config.get("repo_url"))
        return f"https://github.com/{o}/{n}.git"

    def run(self, args, remote=False, input=None, env=None, timeout=900, check=True):
        cmd = ["git"] + LEAN + (self._auth() + ["-c", "core.bigFileThreshold=1m"] if remote else []) + ["--git-dir", str(self.dir)] + args
        try:
            p = subprocess.run(cmd, input=input, capture_output=True, env=self._env(env), timeout=timeout)
        except FileNotFoundError:
            raise GitHubError("`git` is not installed on this server") from None
        except subprocess.TimeoutExpired:
            raise GitHubError(f"git {args[0]} timed out") from None
        if check and p.returncode:
            err = scrub(p.stderr.decode("utf-8", "replace").strip() or p.stdout.decode("utf-8", "replace"))
            if "non-fast-forward" in err or "fetch first" in err or "rejected" in err and "stale" in err:
                raise NonFastForward("the branch moved while pushing")
            raise GitHubError(f"git {args[0]} failed: {err[-600:]}")
        return p

    def ensure(self):
        if not (self.dir / "HEAD").exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--bare", "-q", str(self.dir)], check=True, env=self._env())
        self.run(["config", "remote.origin.url", self.url()])

    def remote_head(self, branch):
        """Commit sha of origin/<branch>, or None if the branch (or repo content) does not exist."""
        p = self.run(["ls-remote", "origin", f"refs/heads/{branch}"], remote=True)
        out = p.stdout.decode().strip()
        return out.split()[0] if out else None

    def fetch(self, branch):
        """Fetch the branch's commit and trees (no blobs). Returns the head sha or None."""
        self.ensure()
        head = self.remote_head(branch)
        if not head:
            return None
        have = self.run(["cat-file", "-e", head + "^{tree}"], check=False)
        if have.returncode:
            self.run(["fetch", "--depth=1", "--filter=blob:none", "--no-tags", "--force", "origin",
                      f"refs/heads/{branch}:refs/remotes/origin/{branch}"], remote=True, timeout=600)
        return head

    @contextlib.contextmanager
    def work(self):
        """A throw-away object directory: blobs hashed here are pushed and then vanish locally."""
        self.ensure()
        tmp = tempfile.mkdtemp(prefix="ark-obj-", dir=str(config.DATA_DIR))
        try:
            yield Work(self, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Work:
    def __init__(self, git, objdir):
        self.git, self.objdir = git, objdir
        self.index = os.path.join(objdir, "index")
        self.extra = {"GIT_OBJECT_DIRECTORY": objdir,
                      "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(git.dir / "objects"),
                      "GIT_INDEX_FILE": self.index}

    def run(self, args, **kw):
        kw["env"] = dict(self.extra, **kw.get("env", {}))
        return self.git.run(args, **kw)

    def hash_file(self, path):
        return self.run(["hash-object", "-w", "--", path]).stdout.decode().strip()

    def drop(self, shas):
        """Forget locally-held blobs that are safely on the remote (frees disk)."""
        for sha in shas:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(self.objdir, sha[:2], sha[2:]))

    def hash_bytes(self, data):
        return self.run(["hash-object", "-w", "--stdin"], input=data).stdout.decode().strip()

    def commit(self, parent, ops, message, branch, force=False, squash=False):
        """Apply `ops` ([(path, mode, sha) | (path, None, None)]) on `parent` and push. Returns the new commit sha."""
        if parent:
            self.run(["read-tree", parent])
        lines = []
        for path, mode, _sha in ops:
            if mode is None:
                lines.append(f"0 {NULL_SHA}\t{path}\n")
        for path, mode, sha in ops:
            if mode is not None:
                lines.append(f"{mode} {sha}\t{path}\n")
        if lines:
            self.run(["update-index", "--add", "--replace", "--index-info"], input="".join(lines).encode())
        tree = self.run(["write-tree", "--missing-ok"]).stdout.decode().strip()
        args = ["commit-tree", tree, "-m", message]
        if parent and not squash:
            args += ["-p", parent]
        commit = self.run(args).stdout.decode().strip()
        push = ["push", "origin", f"{commit}:refs/heads/{branch}"]
        if force or squash:
            push.insert(1, "--force")
        self.run(push, remote=True, timeout=7200)
        return commit
