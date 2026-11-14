import os
import unittest

from arkstorage import config, vfs
from tests.helpers import sandbox


def read(st, p):
    return st.reader(st.idx.files.get(p)).read_all()


class StoreTests(unittest.TestCase):
    def put(self, st, items, **kw):
        with st.tx() as tx:
            for path, data in items.items():
                tx.put_bytes(path, data, **kw)
            return st.commit(tx, "t")

    def test_put_read_split_and_empty(self):
        with sandbox() as (st, _):
            big = os.urandom(2_500_000)
            self.put(st, {"/a/small.txt": b"hi", "/big.bin": big, "/zero": b""})
            self.assertEqual(read(st, "/a/small.txt"), b"hi")
            self.assertEqual(read(st, "/big.bin"), big)
            self.assertEqual(len(st.idx.files.get("/big.bin").parts), 3)
            self.assertEqual(read(st, "/zero"), b"")
            r = st.reader(st.idx.files.get("/big.bin"))
            self.assertEqual(b"".join(r.iter(1_048_570, 1_048_590)), big[1_048_570:1_048_590])

    def test_encryption_and_wrong_passphrase(self):
        with sandbox(encrypt="true", passphrase="pw") as (st, remote):
            data = os.urandom(1_300_000)
            self.put(st, {"/s.bin": data})
            n = st.idx.files.get("/s.bin")
            self.assertTrue(n.enc)
            self.assertEqual(n.size, len(data))
            self.assertEqual(read(st, "/s.bin"), data)
            raw = b"".join(st.cache.path(p[1]).read_bytes() for p in n.parts)
            self.assertNotIn(data[:64], raw)
            config.update({"passphrase": "nope"})
            st._master = None
            with self.assertRaises(vfs.VfsError) as cm:
                read(st, "/s.bin")
            self.assertEqual(cm.exception.status, 423)

    def test_move_copy_rename_and_conflicts(self):
        with sandbox() as (st, _):
            self.put(st, {"/a/x.txt": b"1", "/b/x.txt": b"2", "/n.txt": b"3"})
            with st.tx() as tx:
                tx.move(["/n.txt"], "/a", newname="m.txt")
                tx.move(["/a/x.txt"], "/b", policy="rename")
                st.commit(tx, "mv")
            self.assertEqual(read(st, "/a/m.txt"), b"3")
            self.assertEqual(sorted(x.name for x in st.idx.files.ls("/b")), ["x (1).txt", "x.txt"])
            with st.tx() as tx:
                tx.move(["/a"], "/a/sub")
                with self.assertRaises(vfs.VfsError):
                    st.commit(tx, "bad")
            with st.tx() as tx:
                tx.move(["/b/x (1).txt"], "/b", policy="fail", newname="x.txt")
                with self.assertRaises(vfs.VfsError) as cm:
                    st.commit(tx, "dup")
                self.assertEqual(cm.exception.status, 409)
            with st.tx() as tx:                                   # copy a folder, original stays
                tx.move(["/b"], "/", policy="rename", copy=True)
                st.commit(tx, "cp")
            self.assertEqual(read(st, "/b (1)/x.txt"), b"2")
            self.assertEqual(read(st, "/b/x.txt"), b"2")

    def test_empty_folder_survives_and_trash_roundtrip(self):
        with sandbox() as (st, _):
            self.put(st, {"/d/only.txt": b"x", "/keep.txt": b"k"})
            with st.tx() as tx:
                tx.move(["/d/only.txt"], "/")
                st.commit(tx, "mv")
            self.assertTrue(st.idx.files.get("/d").is_dir)       # folder kept although empty
            with st.tx() as tx:
                tx.delete(["/d", "/keep.txt"])
                st.commit(tx, "rm")
            self.assertIsNone(st.idx.files.get("/keep.txt"))
            self.assertEqual(len(st.idx.trash_meta), 2)
            ids = [k for k, v in st.idx.trash_meta.items() if v["name"] == "keep.txt"]
            with st.tx() as tx:
                tx.restore(ids)
                st.commit(tx, "restore")
            self.assertEqual(read(st, "/keep.txt"), b"k")
            with st.tx() as tx:
                tx.empty_trash()
                st.commit(tx, "empty")
            self.assertEqual(st.idx.trash_meta, {})

    def test_replace_split_with_smaller_and_mtime(self):
        with sandbox() as (st, _):
            self.put(st, {"/f.bin": os.urandom(2_200_000)})
            with st.tx() as tx:
                tx.put_bytes("/f.bin", b"tiny", mtime=1234)
                st.commit(tx, "rep")
            n = st.idx.files.get("/f.bin")
            self.assertEqual((n.size, n.split, n.mtime), (4, False, 1234))
            self.assertEqual(read(st, "/f.bin"), b"tiny")

    def test_convert_and_squash(self):
        with sandbox(passphrase="pw") as (st, _):
            self.put(st, {"/p.txt": b"plain", "/q.txt": b"plain2"})
            st.convert_all(True)
            self.assertTrue(all(n.enc for n in st.idx.files.nodes.values() if not n.is_dir))
            self.assertEqual(read(st, "/p.txt"), b"plain")
            st.convert_all(False)
            self.assertFalse(any(n.enc for n in st.idx.files.nodes.values() if not n.is_dir))
            st.squash()
            self.assertEqual(read(st, "/q.txt"), b"plain2")

    def test_no_config_reports_problem(self):
        with sandbox() as (st, _):
            config.update({"token": None})
            self.assertTrue(st.problems())


if __name__ == "__main__":
    unittest.main()
