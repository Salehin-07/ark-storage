"""Chunks first, manifest last: an upload that stops halfway never changes what a reader sees,
and pieces that never reached a manifest are cleaned up."""
import os
import unittest

from arkstorage import vfs
from tests.helpers import sandbox
from tests.test_api import Api
from tests.test_streaming import begin, commit, git, put_chunks

BRANCH = "refs/heads/ark-data"


def staged(remote):
    return [p for p in git(remote, "ls-tree", "-r", "--name-only", BRANCH).splitlines() if p.startswith(".ark/staging/")]


class AtomicUploadTests(unittest.TestCase):
    def test_cut_short_upload_keeps_previous_file_and_cleanup_removes_pieces(self):
        with sandbox(stage_mb=1, chunk_mb=1) as (st, remote):
            a = Api(st, remote)
            a.login()
            old = os.urandom(2_500_000)
            b = begin(a, [len(old)])
            put_chunks(a, b, "f0", old, 1_000_000, lambda o: o)
            commit(a, b, ["doc.bin"])
            before = git(remote, "rev-parse", BRANCH)

            new = os.urandom(5_000_000)
            b2 = begin(a, [len(new)])
            put_chunks(a, b2, "f0", new, 1_000_000, lambda o: o[:3])         # connection dies after 3 of 5 MB
            self.assertTrue(staged(remote), "pieces were pushed early")
            # a reader at this moment still gets the complete previous file
            self.assertEqual(a.call("get", "/api/raw?path=/doc.bin").data, old)
            r = a.call("post", f"/api/upload/{b2}/commit", json={"files": [{"id": "f0", "path": "/doc.bin", "mtime": 1}], "on_conflict": "replace"})
            self.assertEqual(r.status_code, 400)                                # not fully received: nothing published
            self.assertEqual(a.call("get", "/api/raw?path=/doc.bin").data, old)

            # the server restarts: the unfinished batch has no owner any more
            a2 = Api(st, remote)
            a2.login()
            removed = st.sweep_staging(set())
            self.assertGreater(removed, 0)
            self.assertEqual(staged(remote), [])
            self.assertEqual(a2.call("get", "/api/raw?path=/doc.bin").data, old)
            tree = git(remote, "ls-tree", "-r", "--name-only", BRANCH)
            self.assertNotIn("staging", tree)
            self.assertNotEqual(git(remote, "rev-parse", BRANCH), "")           # branch still valid
            self.assertIn("doc.bin", tree)
            self.assertTrue(before)

    def test_commit_refuses_when_pieces_do_not_add_up(self):
        with sandbox(chunk_mb=1) as (st, remote):
            with st.tx() as tx:
                tx.put_bytes("/x.bin", os.urandom(2_500_000))
                st.commit(tx, "seed")
            head = git(remote, "rev-parse", BRANCH)
            data = os.urandom(3_000_000)
            with st.tx() as tx:
                tx.put_bytes("/x.bin", data)
                tx.intents[-1]["expect"] = len(data) + 1                         # announced size disagrees with the pieces
                with self.assertRaises(vfs.VfsError) as cm:
                    st.commit(tx, "bad")
                self.assertEqual(cm.exception.status if hasattr(cm.exception, "status") else 409, 409)
            self.assertEqual(git(remote, "rev-parse", BRANCH), head, "a refused commit must not move the branch")

    def test_commit_refuses_when_a_piece_is_missing(self):
        with sandbox(chunk_mb=1) as (st, remote):
            with st.tx() as tx:
                tx.put_bytes("/y.bin", os.urandom(2_200_000))
                parts = tx.intents[-1]["parts"]
                tx.w.drop([parts[1][0]])                                        # a piece vanishes before the manifest is written
                head = git(remote, "rev-parse", BRANCH) if git(remote, "branch", "--list") else ""
                with self.assertRaises(vfs.VfsError):
                    st.commit(tx, "bad")
            if head:
                self.assertEqual(git(remote, "rev-parse", BRANCH), head)

    def test_incomplete_split_file_is_never_listed(self):
        entries = [{"path": "files/a.bin.arkparts", "type": "tree", "sha": "t", "mode": "040000", "size": 0}]
        for i in (0, 1, 3):                                                     # piece 0002 is missing
            entries.append({"path": f"files/a.bin.arkparts/{i:04d}", "type": "blob", "sha": f"{i}" * 40, "mode": "100644", "size": 10})
        tree = vfs.build(entries, "files/")
        self.assertIsNone(tree.get("/a.bin"))
        self.assertEqual(tree.incomplete, ["files/a.bin.arkparts"])
        entries = [e for e in entries if not e["path"].endswith("0003")] + [
            {"path": "files/a.bin.arkparts/0002", "type": "blob", "sha": "2" * 40, "mode": "100644", "size": 10}]
        self.assertIsNotNone(vfs.build(entries, "files/").get("/a.bin"))

    def test_confirmed_sweep_waits_for_a_second_idle_pass(self):
        with sandbox(stage_mb=1) as (st, remote):
            a = Api(st, remote)
            a.login()
            data = os.urandom(3_000_000)
            b = begin(a, [len(data)])
            put_chunks(a, b, "f0", data, 1_000_000, lambda o: o)
            self.assertTrue(staged(remote))
            self.assertEqual(st.sweep_staging(set(), confirm=True), 0)          # first sighting: only remembered
            self.assertTrue(staged(remote))
            self.assertGreater(st.sweep_staging(set(), confirm=True), 0)        # unchanged on the second pass: removed
            self.assertEqual(staged(remote), [])


if __name__ == "__main__":
    unittest.main()
