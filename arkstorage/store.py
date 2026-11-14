"""The storage engine: reads through the GitHub API + a disk cache, writes as git commits."""
import base64
import contextlib
import io
import json
import os
import secrets
import shutil
import threading
import time

from . import config, crypto, vfs
from .gh import EMPTY_BLOB, Client, Git, GitHubError, NonFastForward
from .vfs import FILES, META, CONF, TRASH, TRASH_META, SUF_PARTS, VfsError

README = (b"# Ark Storage data\n\nThis branch is managed by [Ark Storage](https://github.com/Salehin-07/ark-storage).\n"
          b"Your files live under `files/` (large files are split into `*.arkparts/` pieces, encrypted ones end in "
          b"`.arkenc`). Edit them through the app, not by hand.\n")


class Cache:
    """Blobs downloaded from GitHub, kept on disk (LRU) so repeated views are instant."""

    def __init__(self, client):
        self.client = client
        self.dir = config.DATA_DIR / "cache"
        self._locks = {}
        self._glock = threading.Lock()

    def path(self, sha):
        return self.dir / sha[:2] / sha

    def ensure(self, sha):
        p = self.path(sha)
        if p.exists():
            with contextlib.suppress(OSError):
                os.utime(p)
            return p
        with self._glock:
            lock = self._locks.setdefault(sha, threading.Lock())
        with lock:
            if not p.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                self.client.download_blob(sha, str(p))
                self.evict(keep=sha)
        return p

    def entries(self):
        out = []
        if self.dir.exists():
            for sub in self.dir.iterdir():
                if sub.is_dir():
                    for f in sub.iterdir():
                        if ".part" not in f.name:
                            with contextlib.suppress(OSError):
                                st = f.stat()
                                out.append((st.st_mtime, st.st_size, f))
        return out

    def size(self):
        return sum(e[1] for e in self.entries())

    def evict(self, keep=None):
        limit = config.get("cache_mb") * 1024 * 1024
        items = sorted(self.entries())
        total = sum(e[1] for e in items)
        for _mt, sz, f in items:
            if total <= limit:
                break
            if f.name == keep:
                continue
            with contextlib.suppress(OSError):
                f.unlink()
                total -= sz

    def clear(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Reader:
    """Random access to the *plain* bytes of a stored file (joins pieces, decrypts)."""

    def __init__(self, store, node, idx):
        self.store, self.node, self.idx = store, node, idx
        self.size = node.size
        self.parts = node.parts
        self.stored = node.stored_size
        self._open = {}

    def _read_stored(self, off, n):
        out = []
        pos = 0
        for _path, sha, sz in self.parts:
            if off < pos + sz and n > 0:
                f = self._open.get(sha)
                if f is None:
                    f = self._open[sha] = open(self.store.cache.ensure(sha), "rb")
                f.seek(off - pos)
                chunk = f.read(min(n, pos + sz - off))
                out.append(chunk)
                off += len(chunk)
                n -= len(chunk)
            pos += sz
            if n <= 0:
                break
        return b"".join(out)

    def iter(self, start=0, end=None, block=1 << 18):
        end = self.size if end is None else min(end, self.size)
        try:
            if self.node.enc:
                master = self.store.master(self.idx)
                try:
                    yield from crypto.decrypt_range(master, self._read_stored, self.stored, start, end)
                except crypto.CryptoError as e:
                    raise VfsError(str(e), 423) from None
            else:
                pos = start
                while pos < end:
                    chunk = self._read_stored(pos, min(block, end - pos))
                    if not chunk:
                        break
                    yield chunk
                    pos += len(chunk)
        finally:
            self.close()

    def read_all(self, limit=None):
        if limit is not None and self.size > limit:
            raise VfsError("File is too large for this action", 413)
        return b"".join(self.iter())

    def close(self):
        for f in self._open.values():
            f.close()
        self._open.clear()


def stage(work, src, enc, master, chunk_bytes):
    """Encrypt (optionally) and cut `src` (file object) into pieces; hash each into the git object store.

    Returns [(sha, size)]. Streams: memory use is one chunk, disk use is one chunk plus the objects.
    """
    parts = []
    state = {"f": None, "n": 0, "path": None}

    def close():
        f = state["f"]
        if f:
            f.close()
            sha = work.hash_file(state["path"])
            parts.append((sha, state["n"]))
            os.remove(state["path"])
            state.update(f=None, n=0)

    class Roll:
        def write(self, b):
            mv = memoryview(b)
            while len(mv):
                if state["f"] is None:
                    state["path"] = os.path.join(work.objdir, f"piece{len(parts)}")
                    state["f"] = open(state["path"], "wb")
                take = min(chunk_bytes - state["n"], len(mv))
                state["f"].write(mv[:take])
                state["n"] += take
                mv = mv[take:]
                if state["n"] >= chunk_bytes:
                    close()

    roll = Roll()
    if enc:
        crypto.encrypt_stream(master, src, roll)
    else:
        while True:
            b = src.read(1 << 20)
            if not b:
                break
            roll.write(b)
    close()
    if not parts:
        parts.append((work.hash_bytes(b""), 0))
    return parts


class Tx:
    """A batch of changes that becomes ONE commit. Use via ``with store.tx() as tx``."""

    def __init__(self, store):
        self.store = store
        self.intents = []
        self.message = ""
        self.results = []
        self._cm = None
        self.w = None

    def __enter__(self):
        self._cm = self.store.git.work()
        self.w = self._cm.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)

    # -- staging ---------------------------------------------------------------------------------
    def put(self, vpath, src, mtime=None, policy="replace", enc=None):
        """Stage a file. `src` is a path or a file object."""
        vpath = vfs.norm(vpath)
        vfs.validate_name(vfs.basename(vpath))
        enc = self.store.encrypt_enabled() if enc is None else enc
        master = self.store.master_for_write() if enc else None
        chunk = config.get("chunk_mb") * 1024 * 1024
        if isinstance(src, (str, os.PathLike)):
            with open(src, "rb") as f:
                parts = stage(self.w, f, enc, master, chunk)
        else:
            parts = stage(self.w, src, enc, master, chunk)
        self.intents.append(dict(op="put", path=vpath, parts=parts, enc=enc, policy=policy,
                                 mtime=int(mtime if mtime is not None else time.time())))

    def put_bytes(self, vpath, data, **kw):
        self.put(vpath, io.BytesIO(data), **kw)

    def mkdir(self, vpath):
        vpath = vfs.norm(vpath)
        vfs.validate_name(vfs.basename(vpath))
        self.intents.append(dict(op="mkdir", path=vpath))

    def delete(self, paths, permanent=False):
        self.intents.append(dict(op="delete", paths=[vfs.norm(p) for p in paths], permanent=permanent))

    def move(self, srcs, dest, policy="rename", newname=None, copy=False):
        self.intents.append(dict(op="copy" if copy else "move", srcs=[vfs.norm(p) for p in srcs],
                                 dest=vfs.norm(dest), policy=policy, newname=newname))

    def restore(self, ids, policy="rename"):
        self.intents.append(dict(op="restore", ids=list(ids), policy=policy))

    def empty_trash(self, ids=None):
        self.intents.append(dict(op="empty_trash", ids=list(ids) if ids else None))

    def set_conf(self, conf):
        self.intents.append(dict(op="conf", conf=conf))


class Store:
    def __init__(self):
        self.client = Client()
        self.git = Git()
        self.cache = Cache(self.client)
        self.idx = None
        self._checked = 0
        self._rlock = threading.RLock()
        self._wlock = threading.RLock()
        self._json_cache = {}
        self._master = None
        self.private = None
        self.last_error = ""

    # -- configuration -------------------------------------------------------------------------
    def problems(self):
        out = []
        if not config.get("repo_url"):
            out.append("Set ARK_REPO_URL to your GitHub repository (or enter it in Settings).")
        if not config.get("token"):
            out.append("Set ARK_GITHUB_TOKEN to a token with access to that repository (or enter it in Settings).")
        return out

    def reset(self):
        """Forget cached state after settings changed."""
        with self._rlock:
            self.idx = None
            self._checked = 0
            self._master = None
            self.private = None
            self.client._etags.clear()

    def encrypt_enabled(self):
        return bool(config.get("encrypt"))

    # -- encryption ----------------------------------------------------------------------------
    def master(self, idx=None):
        idx = idx or self.idx
        phrase = config.get("passphrase")
        if not phrase:
            raise VfsError("This file is encrypted. Enter the passphrase in Settings -> Encryption (or set ARK_PASSPHRASE).", 423)
        conf = idx.conf if idx else {}
        if not conf.get("salt"):
            raise VfsError("Encryption has not been initialised in this repository yet.", 423)
        salt = base64.b64decode(conf["salt"])
        if self._master and self._master[0] == (phrase, conf["salt"]):
            return self._master[1]
        key = crypto.derive_master(phrase, salt)
        try:
            ok = crypto.decrypt_bytes(key, base64.b64decode(conf["check"])) == b"ark-storage"
        except crypto.CryptoError:
            ok = False
        if not ok:
            raise VfsError("Wrong passphrase.", 423)
        self._master = ((phrase, conf["salt"]), key)
        return key

    def master_for_write(self):
        if not config.get("passphrase"):
            raise VfsError("Encryption is on but no passphrase is set. Set one in Settings -> Encryption.", 423)
        idx = self.refresh()
        if not idx.conf.get("salt"):
            self.init_crypto()
            idx = self.refresh(force=True)
        return self.master(idx)

    def init_crypto(self):
        phrase = config.get("passphrase")
        with self._wlock:
            idx = self.refresh(force=True)
            if idx.conf.get("salt"):
                return
            salt = os.urandom(16)
            key = crypto.derive_master(phrase, salt)
            conf = {"v": 1, "salt": base64.b64encode(salt).decode(),
                    "check": base64.b64encode(crypto.encrypt_bytes(key, b"ark-storage")).decode()}
            with self.tx() as tx:
                tx.set_conf(conf)
                self._commit_locked(tx, "Initialise encryption")

    def crypto_state(self):
        idx = self.idx
        return {"enabled": self.encrypt_enabled(), "passphrase_set": bool(config.get("passphrase")),
                "initialised": bool(idx and idx.conf.get("salt"))}

    # -- reading the repository ----------------------------------------------------------------
    def refresh(self, force=False):
        with self._rlock:
            now = time.time()
            if not force and self.idx and now - self._checked < config.get("refresh_seconds"):
                return self.idx
            probs = self.problems()
            if probs:
                raise VfsError(probs[0], 503)
            branch = config.get("branch")
            try:
                res = self.client.tree(branch)
                if res is None:
                    self._init_branch(branch)
                    res = self.client.tree(branch)
                    if res is None:
                        raise GitHubError("The storage branch was created but cannot be read yet; try again.")
            except GitHubError as e:
                self.last_error = str(e)
                raise VfsError(str(e), 502 if not e.status or e.status >= 500 else 503) from None
            self.last_error = ""
            key, entries, changed = res
            if self.idx is None or changed or self.idx.key != key:
                by = {e["path"]: e for e in entries}
                meta = self._load_json(by.get(META))
                conf = self._load_json(by.get(CONF))
                tm = self._load_json(by.get(TRASH_META))
                self.idx = vfs.Index(key, entries, meta, conf, tm)
            self._checked = now
            return self.idx

    def _load_json(self, entry):
        if not entry:
            return {}
        sha = entry["sha"]
        if sha not in self._json_cache:
            try:
                self._json_cache[sha] = json.loads(self.cache.ensure(sha).read_text("utf-8") or "{}")
            except ValueError:
                self._json_cache[sha] = {}
            if len(self._json_cache) > 50:
                self._json_cache.pop(next(iter(self._json_cache)))
        return self._json_cache[sha]

    def _init_branch(self, branch):
        with self._wlock:
            if self.git.fetch(branch):
                return
            with self.git.work() as w:
                sha = w.hash_bytes(README)
                w.commit(None, [("README.md", "100644", sha)], "Initialize Ark Storage", branch)

    def repo_info(self):
        info = self.client.repo()
        self.private = info.get("private")
        return info

    def reader(self, node, idx=None):
        return Reader(self, node, idx or self.idx)

    def stats(self):
        idx = self.refresh()
        files = [n for n in idx.files.nodes.values() if not n.is_dir]
        return {"files": len(files), "folders": sum(1 for n in idx.files.nodes.values() if n.is_dir) - 1,
                "bytes": sum(n.size for n in files), "stored_bytes": sum(n.stored_size for n in files),
                "trash_items": len(idx.trash_meta), "head": idx.key}

    # -- writing -------------------------------------------------------------------------------
    def tx(self):
        return Tx(self)

    def commit(self, tx, message):
        with self._wlock:
            return self._commit_locked(tx, message)

    def _commit_locked(self, tx, message):
        branch = config.get("branch")
        last = None
        for _attempt in range(4):
            idx = self.refresh(force=True)
            ops, results = self._apply(tx, idx)
            if not ops:
                return results
            try:
                parent = self.git.fetch(branch)
                tx.w.commit(parent, ops, message or "Update", branch)
            except NonFastForward as e:
                last = e
                time.sleep(1)
                continue
            except GitHubError as e:
                raise VfsError(str(e), 502) from None
            self.refresh(force=True)
            return results
        raise VfsError(f"Could not push: {last}", 409)

    # -- applying intents to a set of git operations --------------------------------------------
    def _apply(self, tx, idx):
        files = idx.files
        w = tx.w
        adds, dels = {}, set()
        meta, trashm, conf = dict(idx.meta), dict(idx.trash_meta), idx.conf
        dirty = {"meta": False, "trash": False, "conf": False}
        created_files, created_dirs, removed, gone_dirs = set(), set(), set(), set()
        results = []

        def live(v):
            return None if v in removed else files.get(v)

        def check_parents(v):
            p = vfs.parent(v)
            while p != "/":
                n = live(p)
                if (n and not n.is_dir) or p in created_files:
                    raise VfsError(f"'{p}' is a file, not a folder", 409)
                p = vfs.parent(p)

        def siblings(d):
            names = {vfs.basename(p) for p in files.children.get(d, ()) if p not in removed}
            names |= {vfs.basename(p) for p in created_files | created_dirs if vfs.parent(p) == d}
            return names

        def occupied(v):
            return v in created_files or v in created_dirs or live(v) is not None

        def resolve(v, policy, src_is_dir=False):
            """Apply the conflict policy for target `v`. Returns the final vpath or None to skip."""
            if not occupied(v):
                return v
            if policy == "skip":
                return None
            if policy == "rename":
                return vfs.join(vfs.parent(v), vfs.unique_name(vfs.basename(v), siblings(vfs.parent(v))))
            old = live(v)
            if policy == "replace" and old and not old.is_dir and not src_is_dir and v not in created_files:
                return v
            if policy == "replace" and v in created_files:
                return v
            raise VfsError(f"'{vfs.basename(v)}' already exists here", 409)

        def mark_removed(n):
            removed.add(n.path)
            if n.is_dir:
                gone_dirs.add(n.repo)
                removed.update(d.path for d in files.walk(n.path))

        def relocate(n, blobs, new_base):
            out = []
            for e in blobs:
                out.append((e, new_base + e["path"][len(n.repo):]))
            return out

        for it in tx.intents:
            op = it["op"]
            if op == "conf":
                conf = it["conf"]
                dirty["conf"] = True
            elif op == "put":
                v = it["path"]
                check_parents(v)
                old = live(v)
                if old and old.is_dir:
                    v2 = resolve(v, "rename" if it["policy"] == "replace" else it["policy"], False)
                else:
                    v2 = resolve(v, it["policy"])
                if v2 is None:
                    results.append({"path": v, "skipped": True})
                    continue
                if v2 != v:
                    old = None
                    v = v2
                parts, enc = it["parts"], it["enc"]
                split = len(parts) > 1
                base = vfs.repo_path(v, enc, split)
                new = {}
                if split:
                    for i, (sha, _s) in enumerate(parts):
                        new[f"{base}/{i:04d}"] = ("100644", sha)
                else:
                    new[base] = ("100644", parts[0][0])
                if old and not old.is_dir:
                    dels.update(e["path"] for e in idx.node_blobs(old))
                adds.update(new)
                meta[v] = it["mtime"]
                dirty["meta"] = True
                created_files.add(v)
                results.append({"path": v})
            elif op == "mkdir":
                v = it["path"]
                check_parents(v)
                old = live(v)
                if old and not old.is_dir or v in created_files:
                    raise VfsError(f"'{vfs.basename(v)}' already exists as a file", 409)
                adds[FILES + v.strip("/") + "/.arkkeep"] = ("100644", EMPTY_BLOB)
                w.hash_bytes(b"")
                created_dirs.add(v)
                results.append({"path": v})
            elif op == "delete":
                for p in it["paths"]:
                    n = live(p)
                    if not n or p == "/":
                        raise VfsError(f"'{p}' was not found", 404)
                    blobs = idx.node_blobs(n)
                    permanent = it["permanent"] or not config.get("trash")
                    if permanent:
                        dels.update(e["path"] for e in blobs)
                    else:
                        tid = secrets.token_hex(5)
                        top = vfs.basename(n.repo)
                        for e, newp in relocate(n, blobs, f"{TRASH}{tid}/{top}"):
                            dels.add(e["path"])
                            adds[newp] = (e["mode"], e["sha"])
                        m = {"": meta.get(p, 0)} if not n.is_dir else {
                            k[len(p):]: v for k, v in meta.items() if k.startswith(p + "/")}
                        trashm[tid] = {"path": p, "name": n.name, "dir": n.is_dir, "ts": int(time.time()),
                                       "size": n.size, "m": m}
                        dirty["trash"] = True
                    for k in [k for k in meta if k == p or k.startswith(p + "/")]:
                        meta.pop(k)
                        dirty["meta"] = True
                    mark_removed(n)
                    results.append({"path": p})
            elif op in ("move", "copy"):
                copy = op == "copy"
                dest = it["dest"]
                check_parents(vfs.join(dest, "x"))
                dn = live(dest)
                if dn and not dn.is_dir or dest in created_files:
                    raise VfsError("Destination is not a folder", 409)
                for sp in it["srcs"]:
                    n = live(sp)
                    if not n or sp == "/":
                        raise VfsError(f"'{sp}' was not found", 404)
                    name = vfs.validate_name(it["newname"]) if it.get("newname") else n.name
                    if n.is_dir and (dest == sp or dest.startswith(sp + "/")):
                        raise VfsError("A folder can't be placed inside itself", 400)
                    v = vfs.join(dest, name)
                    if v == sp and not copy:
                        results.append({"path": v})
                        continue
                    v2 = resolve(v, it["policy"], n.is_dir)
                    if v2 is None:
                        results.append({"path": v, "skipped": True})
                        continue
                    old = live(v2)
                    if old and not old.is_dir and not n.is_dir and v2 not in created_files:
                        dels.update(e["path"] for e in idx.node_blobs(old))
                        mark_removed(old)
                    v = v2
                    base = FILES + v.strip("/") if n.is_dir else vfs.repo_path(v, n.enc, n.split)
                    for e, newp in relocate(n, idx.node_blobs(n), base):
                        adds[newp] = (e["mode"], e["sha"])
                        if not copy:
                            dels.add(e["path"])
                    for k in [k for k in list(meta) if k == sp or k.startswith(sp + "/")]:
                        meta[v + k[len(sp):]] = meta[k]
                        if not copy:
                            meta.pop(k)
                        dirty["meta"] = True
                    (created_dirs if n.is_dir else created_files).add(v)
                    if not copy:
                        mark_removed(n)
                    results.append({"path": v})
            elif op == "restore":
                for tid in it["ids"]:
                    item = trashm.get(tid)
                    tn = idx.trash.ls("/" + tid)
                    if not item or not tn:
                        raise VfsError("Item is no longer in the trash", 404)
                    n = tn[0]
                    v = item["path"]
                    check_parents(v)
                    v = resolve(v, it["policy"], n.is_dir) or v
                    base = FILES + v.strip("/") if n.is_dir else vfs.repo_path(v, n.enc, n.split)
                    for e, newp in relocate(n, idx.node_blobs(n), base):
                        dels.add(e["path"])
                        adds[newp] = (e["mode"], e["sha"])
                    for rel, mt in item.get("m", {}).items():
                        meta[v + rel] = mt
                        dirty["meta"] = True
                    (created_dirs if n.is_dir else created_files).add(v)
                    trashm.pop(tid)
                    dirty["trash"] = True
                    results.append({"path": v})
            elif op == "empty_trash":
                if it.get("ids"):
                    for tid in it["ids"]:
                        dels.update(e["path"] for e in idx.blobs_under(TRASH + tid))
                        trashm.pop(tid, None)
                else:
                    dels.update(p for p, e in idx.entries.items() if p.startswith(TRASH) and e["type"] == "blob")
                    trashm = {}
                dirty["trash"] = True

        for p in list(dels):
            if p in adds:
                dels.discard(p)

        # keep folders alive that lost their last file (git cannot store empty folders)
        if dels:
            need = set()
            for p in dels:
                if not p.startswith(FILES):
                    continue
                d = vfs.parent_repo(p)
                if d.endswith(SUF_PARTS):
                    d = vfs.parent_repo(d)
                while d and d != FILES.rstrip("/"):
                    need.add(d)
                    d = vfs.parent_repo(d)
            for d in sorted(need, key=len, reverse=True):
                if any(d == g or d.startswith(g + "/") for g in gone_dirs):
                    continue
                if idx.has_prefix(d + "/", dels) or any(a.startswith(d + "/") for a in adds):
                    continue
                adds[d + "/.arkkeep"] = ("100644", EMPTY_BLOB)
                w.hash_bytes(b"")

        def put_json(path, obj):
            adds[path] = ("100644", w.hash_bytes(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()))

        if dirty["meta"]:
            put_json(META, meta)
        if dirty["trash"]:
            put_json(TRASH_META, trashm)
        if dirty["conf"]:
            put_json(CONF, conf)
        ops = [(p, None, None) for p in sorted(dels)] + [(p, m, s) for p, (m, s) in adds.items()]
        return ops, results

    # -- maintenance ---------------------------------------------------------------------------
    def squash(self):
        """Replace the branch history by one commit with the same files (frees space on GitHub)."""
        branch = config.get("branch")
        with self._wlock:
            self.refresh(force=True)
            parent = self.git.fetch(branch)
            with self.git.work() as w:
                w.commit(parent, [], "Ark Storage: history compacted", branch, squash=True)
            self.client._etags.clear()
            return self.refresh(force=True).key

    def convert_all(self, to_encrypted, progress=None, batch_bytes=200 * 1024 * 1024):
        """Re-store every file encrypted (or decrypted). Works in batches of one commit each."""
        idx = self.refresh(force=True)
        todo = [n for n in idx.files.nodes.values() if not n.is_dir and n.enc != to_encrypted]
        total = len(todo)
        done = 0
        i = 0
        while i < total:
            size = 0
            batch = []
            while i < total and (not batch or size + todo[i].size <= batch_bytes):
                batch.append(todo[i])
                size += todo[i].size
                i += 1
            with self.tx() as tx:
                for n in batch:
                    cur = self.refresh().files.get(n.path) or n
                    r = self.reader(cur)
                    tmp = tx.w.objdir + "/convert.tmp"
                    with open(tmp, "wb") as f:
                        for chunk in r.iter():
                            f.write(chunk)
                    tx.put(n.path, tmp, mtime=n.mtime, policy="replace", enc=to_encrypted)
                    os.remove(tmp)
                    done += 1
                    if progress:
                        progress(done, total, n.path)
                self.commit(tx, f"{'Encrypt' if to_encrypted else 'Decrypt'} {len(batch)} file(s)")
        return total
