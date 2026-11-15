"""The virtual file system on top of a git tree.

Repository layout (branch ``ark-data`` by default)::

    README.md
    .ark/config.json     encryption salt + passphrase check (no secrets)
    .ark/meta.json       modification times      {"/path": unix_seconds}
    .ark/trash.json      what is in the trash    {id: {...}}
    files/...            your files, at their real paths
    trash/<id>/...       deleted items (kept until you empty the trash)

How one virtual file is stored (so everything stays visible and diffable on GitHub):

* ``name``                          plain file, up to the chunk size
* ``name.arkparts/0000 0001 ...``   plain file split into pieces
* ``name.arkenc``                   encrypted file
* ``name.arkenc.arkparts/0000 ...`` encrypted file split into pieces
* ``dir/.arkkeep``                  keeps an empty folder alive (git cannot store empty folders)

Renames, moves and copies reuse the same blob ids, so they never re-upload data.
"""
import bisect
import re
from collections import defaultdict

from . import crypto

FILES = "files/"
TRASH = "trash/"
META = ".ark/meta.json"
CONF = ".ark/config.json"
TRASH_META = ".ark/trash.json"
SUF_PARTS = ".arkparts"
SUF_ENC = ".arkenc"
KEEP = ".arkkeep"


class VfsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Node:
    __slots__ = ("path", "name", "is_dir", "size", "enc", "split", "repo", "sha", "parts", "mtime")

    def __init__(self, path, name, is_dir, size=0, enc=False, split=False, repo="", sha="", parts=None, mtime=0):
        self.path, self.name, self.is_dir, self.size = path, name, is_dir, size
        self.enc, self.split, self.repo, self.sha = enc, split, repo, sha
        self.parts = parts or []          # [(repo_path, sha, stored_size)] in order
        self.mtime = mtime

    @property
    def stored_size(self):
        return sum(p[2] for p in self.parts)


def norm(path):
    """Normalise a user path to '/a/b' (no '..', no empty parts). Root is '/'."""
    parts = []
    for seg in str(path or "/").replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise VfsError("'..' is not allowed in paths")
        parts.append(seg)
    return "/" + "/".join(parts)


def parent(path):
    return path.rsplit("/", 1)[0] or "/"


def basename(path):
    return path.rsplit("/", 1)[-1]


def join(directory, name):
    return ("/" + name) if directory == "/" else f"{directory}/{name}"


def validate_name(name):
    if not name or name in (".", "..") or name != name.strip() and not name.strip():
        raise VfsError("Name can't be empty")
    if "/" in name or "\\" in name or "\x00" in name or re.search(r"[\x00-\x1f\x7f]", name):
        raise VfsError("Name can't contain slashes or control characters")
    if len(name.encode("utf-8")) > 200:
        raise VfsError("Name is too long (200 bytes max)")
    low = name.lower()
    if low == ".git" or low.endswith((SUF_PARTS, SUF_ENC)) or low == KEEP:
        raise VfsError(f"Names ending in {SUF_PARTS} or {SUF_ENC}, or called {KEEP} or .git, are reserved")
    return name


def stored_name(name, enc, split):
    return name + (SUF_ENC if enc else "") + (SUF_PARTS if split else "")


def repo_path(vpath, enc=False, split=False):
    """Repo path of the node stored for virtual `vpath` (blob for single files, tree for split files)."""
    d, n = parent(vpath), basename(vpath)
    return FILES + (d.strip("/") + "/" if d != "/" else "") + stored_name(n, enc, split)


def unique_name(name, taken):
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    else:
        ext = "." + ext
    i = 1
    while True:
        cand = f"{stem} ({i}){ext}"
        if cand not in taken:
            return cand
        i += 1


class Tree:
    """Read-only view: nodes by virtual path, children by directory."""

    def __init__(self):
        self.nodes = {"/": Node("/", "", True, repo="")}
        self.children = defaultdict(list)
        self.incomplete = []            # repo paths of split files with missing pieces: never shown, never served

    def get(self, path):
        return self.nodes.get(path)

    def ls(self, path):
        return [self.nodes[p] for p in self.children.get(path, ())]

    def walk(self, path):
        """All descendants of directory `path` (depth first)."""
        stack = list(self.children.get(path, ()))[::-1]
        while stack:
            p = stack.pop()
            n = self.nodes[p]
            yield n
            if n.is_dir:
                stack.extend(self.children.get(p, ())[::-1])

    def _add(self, node):
        self.nodes[node.path] = node
        self.children[parent(node.path)].append(node.path)


def _ensure_dirs(tree, vpath, repo_dir):
    """Make sure every directory above `vpath` exists in the tree."""
    chain = []
    p = parent(vpath)
    while p != "/" and p not in tree.nodes:
        chain.append(p)
        p = parent(p)
    for d in reversed(chain):
        tree._add(Node(d, basename(d), True, repo=repo_dir(d)))


def build(entries, prefix, meta=None):
    """Build a Tree from git tree entries (dicts: path, type, sha, size, mode) below `prefix`."""
    meta = meta or {}
    tree = Tree()
    parts_of = defaultdict(list)
    for e in entries:
        p = e["path"]
        if e["type"] == "blob" and p.startswith(prefix) and parent_repo(p).endswith(SUF_PARTS):
            parts_of[parent_repo(p)].append(e)

    def repo_dir(v):
        return prefix + v.strip("/")

    for e in sorted(entries, key=lambda x: x["path"]):
        p = e["path"]
        if not p.startswith(prefix):
            continue
        if parent_repo(p).endswith(SUF_PARTS):
            continue
        rel = p[len(prefix):]
        name = rel.rsplit("/", 1)[-1]
        vdir = "/" + rel.rsplit("/", 1)[0] if "/" in rel else "/"
        if e["type"] == "tree" and not name.endswith(SUF_PARTS):
            v = join(vdir, name)
            if v not in tree.nodes:
                _ensure_dirs(tree, v, repo_dir)
                tree._add(Node(v, name, True, repo=p, sha=e["sha"]))
            continue
        if e["type"] == "tree":                        # split file
            pieces = sorted(parts_of.get(p, []), key=lambda x: x["path"])
            if not pieces:
                continue
            if [x["path"].rsplit("/", 1)[-1] for x in pieces] != [f"{k:04d}" for k in range(len(pieces))]:
                tree.incomplete.append(p)                # a piece is missing: a reader must never see a half file
                continue
            parts = [(x["path"], x["sha"], x.get("size", 0)) for x in pieces]
            _file(tree, vdir, name[:-len(SUF_PARTS)], True, parts, p, e["sha"], meta, repo_dir)
        elif e["type"] == "blob":
            if name == KEEP:
                if vdir != "/" and vdir not in tree.nodes:
                    _ensure_dirs(tree, join(vdir, "x"), repo_dir)
                    tree._add(Node(vdir, basename(vdir), True, repo=prefix + vdir.strip("/")))
                continue
            _file(tree, vdir, name, False, [(p, e["sha"], e.get("size", 0))], p, e["sha"], meta, repo_dir)
    return tree


def _file(tree, vdir, stored, split, parts, repo, sha, meta, repo_dir):
    enc = stored.endswith(SUF_ENC)
    name = stored[:-len(SUF_ENC)] if enc else stored
    if not name:
        return
    total = sum(p[2] for p in parts)
    try:
        size = crypto.plain_size(total) if enc else total
    except crypto.CryptoError:
        size = total
    v = join(vdir, name)
    _ensure_dirs(tree, v, repo_dir)
    if v in tree.nodes:                                   # duplicate representation: keep the first
        return
    tree._add(Node(v, name, False, size, enc, split, repo, sha, parts, meta.get(v, 0)))


def parent_repo(p):
    return p.rsplit("/", 1)[0] if "/" in p else ""


class Index:
    """Everything known about one commit of the storage branch."""

    def __init__(self, key, entries, meta, conf, trash_meta):
        self.key = key
        self.entries = {e["path"]: e for e in entries}
        self.keys = sorted(self.entries)
        self.meta, self.conf, self.trash_meta = meta, conf, trash_meta
        self.files = build(entries, FILES, meta)
        self.trash = build(entries, TRASH, {})
        self.sha_of = {p: e["sha"] for p, e in self.entries.items()}

    def blobs_under(self, repo_prefix):
        """Blob entries at or below a repo path (a blob itself, or every blob inside a tree)."""
        out = []
        e = self.entries.get(repo_prefix)
        if e and e["type"] == "blob":
            return [e]
        pre = repo_prefix + "/"
        i = bisect.bisect_left(self.keys, pre)
        while i < len(self.keys) and self.keys[i].startswith(pre):
            e = self.entries[self.keys[i]]
            if e["type"] == "blob":
                out.append(e)
            i += 1
        return out

    def node_blobs(self, node):
        if node.is_dir:
            return self.blobs_under(node.repo) if node.path != "/" else self.blobs_under(FILES.rstrip("/"))
        return [self.entries[p[0]] for p in node.parts if p[0] in self.entries]

    def has_prefix(self, pre, ignore):
        i = bisect.bisect_left(self.keys, pre)
        while i < len(self.keys) and self.keys[i].startswith(pre):
            k = self.keys[i]
            if k not in ignore and self.entries[k]["type"] == "blob":
                return True
            i += 1
        return False
