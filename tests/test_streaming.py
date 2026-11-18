"""Uploads stream to GitHub in rolling batches: bounded local disk, hidden staging commits, clean result."""
import os
import subprocess
import threading
import unittest

from tests.helpers import ApiCase, sandbox
from tests.api_harness import Api


def git(remote, *a):
    return subprocess.run(["git", "--git-dir", remote, *a], capture_output=True, check=True).stdout.decode()


def put_chunks(a, b, fid, data, step, order):
    offs = list(range(0, max(1, len(data)), step))
    for i in order(offs):
        r = a.call("put", f"/api/upload/{b}/{fid}?offset={i}", data=data[i:i + step])
        assert r.status_code == 200, r.get_json()


def begin(a, sizes):
    return a.call("post", "/api/upload/begin", json={"files": [{"id": f"f{i}", "size": s} for i, s in enumerate(sizes)]}).get_json()["batch"]


def commit(a, b, names, policy="rename"):
    r = a.call("post", f"/api/upload/{b}/commit", json={"files": [{"id": f"f{i}", "path": "/" + n, "mtime": 1} for i, n in enumerate(names)], "on_conflict": policy})
    assert r.status_code == 200, r.get_json()
    return a.job(r)


class StreamingTests(ApiCase):
    def setUp(self):
        # record whether the final push had to lazily fetch freed blobs back (a pack appearing in the work dir)
        from arkstorage import gh
        self.refetched = []
        orig = gh.Work.commit

        def spy(w, *a, **kw):
            r = orig(w, *a, **kw)
            pk = os.path.join(w.objdir, "pack")
            if os.path.isdir(pk) and any(f.endswith(".pack") for f in os.listdir(pk)):
                self.refetched.append(w.objdir)
            return r
        gh.Work.commit = spy
        self.addCleanup(setattr, gh.Work, "commit", orig)

    def check(self, enc):
        extra = {"stage_mb": 1, "encrypt": True, "passphrase": "pw"} if enc else {"stage_mb": 1}
        with sandbox(**extra) as (st, remote):
            a = Api(st, remote)
            a.login()
            big, small = os.urandom(7_300_000), os.urandom(500_000)
            b = begin(a, [len(big), len(small), 0])
            # out-of-order chunks, a duplicated retry, a zero-byte file
            put_chunks(a, b, "f0", big, 1_000_000, lambda o: o[::-1])
            put_chunks(a, b, "f0", big, 1_000_000, lambda o: o[:2])            # retry of finished data is harmless
            put_chunks(a, b, "f1", small, 1_000_000, lambda o: o)
            put_chunks(a, b, "f2", b"", 1_000_000, lambda o: o)
            def disk(path):
                return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)
            held = sum(disk(os.path.join(st.cache.dir.parent, n)) for n in os.listdir(st.cache.dir.parent) if n.startswith("ark-obj-"))
            self.assertLess(held, 4_500_000, "pushed pieces must be freed locally (7.8 MB uploaded)")
            self.assertGreater(a.st.idx.entries and git(remote, "log", "--oneline", "refs/heads/ark-data").count("staging upload"), 0)
            commit(a, b, ["big.bin", "small.bin", "empty.bin"])
            self.assertEqual(self.refetched, [], "freed pieces must never be downloaded again")
            self.assertEqual(a.call("get", "/api/raw?path=/big.bin").data, big)
            self.assertEqual(a.call("get", "/api/raw?path=/small.bin").data, small)
            self.assertEqual(a.call("get", "/api/raw?path=/empty.bin").data, b"")
            tree = git(remote, "ls-tree", "-r", "--name-only", "refs/heads/ark-data")
            self.assertNotIn(".ark/staging", tree)
            self.assertEqual(os.listdir(st.cache.dir.parent / "uploads"), [])
            leftovers = [p for p in os.listdir(st.cache.dir.parent) if p.startswith("ark-obj-")]
            self.assertEqual(leftovers, [])

    def test_plain(self):
        self.check(False)

    def test_encrypted(self):
        self.check(True)

    def test_parallel_chunks(self):
        with sandbox(stage_mb=1) as (st, remote):
            a = Api(st, remote)
            a.login()
            data = os.urandom(6_000_000)
            b = begin(a, [len(data)])
            ts = [threading.Thread(target=lambda o=o: a.call("put", f"/api/upload/{b}/f0?offset={o}", data=data[o:o + 1_000_000]).status_code)
                  for o in range(0, len(data), 1_000_000)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            commit(a, b, ["p.bin"])
            self.assertEqual(a.call("get", "/api/raw?path=/p.bin").data, data)

    def test_cancel_and_abandoned_cleanup(self):
        with sandbox(stage_mb=1) as (st, remote):
            a = Api(st, remote)
            a.login()
            data = os.urandom(4_000_000)
            b = begin(a, [len(data)])
            put_chunks(a, b, "f0", data, 1_000_000, lambda o: o)
            self.assertIn(".ark/staging", git(remote, "ls-tree", "-r", "--name-only", "refs/heads/ark-data"))
            a.call("post", f"/api/upload/{b}/cancel", json={})
            self.assertNotIn(".ark/staging", git(remote, "ls-tree", "-r", "--name-only", "refs/heads/ark-data"))
            # an upload abandoned by a restart: staging data of an unknown batch is swept on the next begin
            b2 = begin(a, [len(data)])
            put_chunks(a, b2, "f0", data, 1_000_000, lambda o: o)
            a2 = Api(st, remote)           # fresh app instance = server restart (no live uploads)
            a2.login()
            begin(a2, [10])
            self.assertNotIn(".ark/staging", git(remote, "ls-tree", "-r", "--name-only", "refs/heads/ark-data"))


if __name__ == "__main__":
    unittest.main()
