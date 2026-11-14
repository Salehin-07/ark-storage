import io
import os
import time
import unittest
import zipfile

from arkstorage.app import create_app
from tests.helpers import FakeClient, FakeGit, sandbox


class Api:
    def __init__(self, store, remote):
        self.app = create_app()
        st = self.app.ark_store
        st.client, st.git = FakeClient(remote), FakeGit(remote)
        st.cache.client, st.cache.dir = st.client, st.cache.dir
        self.st, self.c = st, self.app.test_client()
        self.csrf = ""

    def login(self, user="ark", pw="ark"):
        r = self.c.post("/api/login", json={"username": user, "password": pw})
        if r.status_code == 200:
            self.csrf = r.get_json()["csrf"]
        return r

    def call(self, method, url, **kw):
        h = kw.pop("headers", {})
        h["X-CSRF-Token"] = self.csrf
        return getattr(self.c, method)(url, headers=h, **kw)

    def job(self, resp):
        jid = resp.get_json()["job"]["id"]
        for _ in range(200):
            j = self.call("get", f"/api/jobs/{jid}").get_json()
            if j["state"] != "running":
                assert j["state"] == "done", j
                return j
            time.sleep(0.1)
        raise AssertionError("job timeout")

    def upload(self, files, dest="/", policy="rename"):
        b = self.call("post", "/api/upload/begin", json={"files": [{"id": f"f{i}", "size": len(d)} for i, (n, d) in enumerate(files)]}).get_json()["batch"]
        for i, (_n, d) in enumerate(files):
            for off in range(0, max(1, len(d)), 700_000):
                r = self.call("put", f"/api/upload/{b}/f{i}?offset={off}", data=d[off:off + 700_000])
                assert r.status_code == 200, r.get_json()
        r = self.call("post", f"/api/upload/{b}/commit", json={"files": [
            {"id": f"f{i}", "path": (dest.rstrip("/") + "/" + n), "mtime": 1700000000000} for i, (n, d) in enumerate(files)], "on_conflict": policy})
        assert r.status_code == 200, r.get_json()
        return self.job(r)


class ApiTests(unittest.TestCase):
    def test_auth_required_and_csrf(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            self.assertEqual(a.c.get("/api/list").status_code, 401)
            self.assertEqual(a.login("ark", "wrong").status_code, 401)
            self.assertEqual(a.login().status_code, 200)
            self.assertTrue(a.c.get("/api/session").get_json()["default_password"])
            r = a.c.post("/api/mkdir", json={"path": "/x"})            # no CSRF header
            self.assertEqual(r.status_code, 403)
            r = a.call("post", "/api/mkdir", json={"path": "/x"})
            self.assertEqual(r.status_code, 200, r.get_json())

    def test_login_rate_limit(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            codes = [a.login("ark", "bad").status_code for _ in range(8)]
            self.assertIn(429, codes)

    def test_full_flow(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            big = os.urandom(2_600_000)
            a.upload([("hello.txt", b"hello\n"), ("big.bin", big)])
            d = a.call("get", "/api/list?path=/").get_json()
            names = {e["name"]: e for e in d["entries"]}
            self.assertEqual(names["big.bin"]["parts"], 3)
            self.assertEqual(names["hello.txt"]["mtime"], 1700000000)
            # raw, ranges, conditional
            r = a.call("get", "/api/raw?path=/big.bin")
            self.assertEqual(r.data, big)
            r = a.call("get", "/api/raw?path=/big.bin", headers={"Range": "bytes=1048570-1048590"})
            self.assertEqual((r.status_code, r.data), (206, big[1048570:1048591]))
            self.assertEqual(r.headers["Content-Range"], f"bytes 1048570-1048590/{len(big)}")
            r = a.call("get", "/api/raw?path=/big.bin", headers={"Range": "bytes=-5"})
            self.assertEqual(r.data, big[-5:])
            self.assertEqual(a.call("get", "/api/raw?path=/big.bin", headers={"Range": "bytes=999999999-"}).status_code, 416)
            self.assertIn("attachment", a.call("get", "/api/raw?path=/hello.txt&download=1").headers["Content-Disposition"])
            # text edit with conflict detection
            t = a.call("get", "/api/text?path=/hello.txt").get_json()
            self.assertEqual(t["content"], "hello\n")
            ok = a.call("put", "/api/text", json={"path": "/hello.txt", "content": "changed", "sha": t["sha"]})
            self.assertEqual(ok.status_code, 200)
            stale = a.call("put", "/api/text", json={"path": "/hello.txt", "content": "again", "sha": t["sha"]})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(a.call("get", "/api/raw?path=/hello.txt").data, b"changed")
            # binary/compressed files are refused by the editor
            self.assertEqual(a.call("get", "/api/text?path=/big.bin").status_code, 415)
            a.upload([("a.zip", b"PK")])
            self.assertEqual(a.call("get", "/api/text?path=/a.zip").status_code, 415)
            # folders, rename, move, search, zip download
            self.assertEqual(a.call("post", "/api/mkdir", json={"path": "/docs"}).status_code, 200)
            self.assertEqual(a.call("post", "/api/move", json={"paths": ["/hello.txt"], "dest": "/docs"}).status_code, 200)
            self.assertEqual(a.call("post", "/api/rename", json={"path": "/docs/hello.txt", "name": "hi.txt"}).status_code, 200)
            s = a.call("get", "/api/search?q=HI.T").get_json()["entries"]
            self.assertEqual([e["path"] for e in s], ["/docs/hi.txt"])
            z = a.call("get", "/api/zip?p=/docs&name=d")
            zf = zipfile.ZipFile(io.BytesIO(z.data))
            self.assertEqual(zf.read("docs/hi.txt"), b"changed")
            # delete -> trash -> restore
            self.assertEqual(a.call("post", "/api/delete", json={"paths": ["/docs"]}).status_code, 200)
            tr = a.call("get", "/api/trash").get_json()["items"]
            self.assertEqual(tr[0]["name"], "docs")
            self.assertEqual(a.call("post", "/api/trash/restore", json={"ids": [tr[0]["id"]]}).status_code, 200)
            self.assertEqual(a.call("get", "/api/raw?path=/docs/hi.txt").data, b"changed")
            # share link works without a session
            link = a.call("post", "/api/share", json={"path": "/docs/hi.txt", "hours": 1}).get_json()["url"]
            anon = a.app.test_client()
            self.assertEqual(anon.get(link.replace("http://localhost", "")).data, b"changed")
            self.assertEqual(anon.get("/s/garbage").status_code, 404)
            # compress + extract round trip
            a.job(a.call("post", "/api/compress", json={"paths": ["/docs"], "dest": "/", "name": "docs.zip"}))
            a.job(a.call("post", "/api/extract", json={"path": "/docs.zip"}))
            self.assertEqual(a.call("get", "/api/raw?path=/docs/docs/hi.txt").data, b"changed")

    def test_upload_conflict_policies_and_path_safety(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            a.upload([("f.txt", b"1")])
            a.upload([("f.txt", b"2")], policy="rename")
            a.upload([("f.txt", b"3")], policy="skip")
            names = sorted(e["name"] for e in a.call("get", "/api/list?path=/").get_json()["entries"])
            self.assertEqual(names, ["f (1).txt", "f.txt"])
            a.upload([("f.txt", b"4")], policy="replace")
            self.assertEqual(a.call("get", "/api/raw?path=/f.txt").data, b"4")
            self.assertEqual(a.call("get", "/api/list?path=/../etc").status_code, 400)
            r = a.call("post", "/api/mkdir", json={"path": "/bad.arkparts"})
            self.assertEqual(r.status_code, 400)

    def test_settings_encryption_flow(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            s = a.call("get", "/api/settings").get_json()["settings"]
            self.assertTrue(s["token"]["secret"] and s["token"]["value"] == "")
            self.assertEqual(a.call("put", "/api/settings", json={"changes": {"chunk_mb": 500}}).status_code, 400)
            r = a.call("put", "/api/settings", json={"changes": {"encrypt": True, "passphrase": "pw"}})
            self.assertEqual(r.status_code, 200)
            a.upload([("s.txt", b"secret text")])
            e = a.call("get", "/api/list?path=/").get_json()["entries"][0]
            self.assertTrue(e["enc"])
            self.assertEqual(a.call("get", "/api/raw?path=/s.txt").data, b"secret text")
            bad = a.call("put", "/api/settings", json={"changes": {"passphrase": "other"}})
            self.assertEqual(bad.status_code, 400)
            a.job(a.call("post", "/api/admin/convert", json={"encrypted": False}))
            e = a.call("get", "/api/list?path=/").get_json()["entries"][0]
            self.assertFalse(e["enc"])

    def test_account_change(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            self.assertEqual(a.call("post", "/api/account", json={"current": "x", "password": "newpass"}).status_code, 400)
            self.assertEqual(a.call("post", "/api/account", json={"current": "ark", "username": "me", "password": "newpass"}).status_code, 200)
            b = Api(st, remote)
            self.assertEqual(b.login("ark", "ark").status_code, 401)
            self.assertEqual(b.login("me", "newpass").status_code, 200)
            self.assertFalse(b.c.get("/api/session").get_json()["default_password"])

    def test_pages(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            html = a.c.get("/").get_data(as_text=True)
            self.assertIn("Ark Storage", html)
            for js in ("files-core.js", "editor-core.js", "ui.js", "editor.js", "settings.js", "app.js", "style.css"):
                self.assertEqual(a.c.get("/static/" + js).status_code, 200, js)
            self.assertEqual(a.c.get("/static/../app.py").status_code, 404)
            self.assertEqual(a.c.get("/healthz").get_json()["ok"], True)


if __name__ == "__main__":
    unittest.main()
