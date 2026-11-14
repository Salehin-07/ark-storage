import unittest

from arkstorage import vfs


def E(p, t, s=0):
    return {"path": p, "type": t, "sha": "a" * 40, "size": s, "mode": "100644" if t == "blob" else "040000"}


class VfsTests(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(vfs.norm("a//b/./c/"), "/a/b/c")
        self.assertEqual(vfs.norm(""), "/")
        with self.assertRaises(vfs.VfsError):
            vfs.norm("a/../b")

    def test_validate_name(self):
        for bad in ("", ".", "..", "a/b", "x.arkparts", "y.ARKENC", ".arkkeep", ".git", "a\x01"):
            with self.assertRaises(vfs.VfsError, msg=bad):
                vfs.validate_name(bad)
        self.assertEqual(vfs.validate_name("দৈনিক.txt"), "দৈনিক.txt")

    def test_unique_name(self):
        self.assertEqual(vfs.unique_name("a.txt", {"a.txt"}), "a (1).txt")
        self.assertEqual(vfs.unique_name("a.txt", {"a.txt", "a (1).txt"}), "a (2).txt")
        self.assertEqual(vfs.unique_name("noext", {"noext"}), "noext (1)")
        self.assertEqual(vfs.unique_name(".env", {".env"}), ".env (1)")

    def test_build_layouts(self):
        ents = [E("files", "tree"), E("files/a", "tree"), E("files/a/x.txt", "blob", 5),
                E("files/a/big.bin.arkparts", "tree"), E("files/a/big.bin.arkparts/0000", "blob", 100),
                E("files/a/big.bin.arkparts/0001", "blob", 50), E("files/e", "tree"), E("files/e/.arkkeep", "blob"),
                E("files/s.txt.arkenc", "blob", 22 + 16 + 4), E("trash", "tree")]
        i = vfs.Index("k", ents, {"/a/x.txt": 7}, {}, {})
        n = i.files.nodes
        self.assertEqual((n["/a/big.bin"].size, n["/a/big.bin"].split), (150, True))
        self.assertTrue(n["/e"].is_dir and not i.files.ls("/e"))
        self.assertEqual((n["/s.txt"].enc, n["/s.txt"].size), (True, 4))
        self.assertEqual(n["/a/x.txt"].mtime, 7)
        self.assertEqual(sorted(x.path for x in i.files.ls("/a")), ["/a/big.bin", "/a/x.txt"])
        self.assertEqual([b["path"] for b in i.node_blobs(n["/a/big.bin"])],
                         ["files/a/big.bin.arkparts/0000", "files/a/big.bin.arkparts/0001"])
        self.assertEqual(len(i.node_blobs(n["/a"])), 3)


if __name__ == "__main__":
    unittest.main()
