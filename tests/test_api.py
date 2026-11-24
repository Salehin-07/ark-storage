import io
import os
import unittest
import zipfile

from tests.api_harness import Api
from tests.helpers import ApiCase, sandbox


class ApiTests(ApiCase):
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
            anon = a.anon()
            self.assertEqual(anon.get(link.replace("http://localhost", "")).data, b"changed")
            self.assertEqual(anon.get("/s/garbage").status_code, 404)
            # compress + extract round trip
            a.job(a.call("post", "/api/compress", json={"paths": ["/docs"], "dest": "/", "name": "docs.zip"}))
            a.job(a.call("post", "/api/extract", json={"path": "/docs.zip"}))
            self.assertEqual(a.call("get", "/api/raw?path=/docs/docs/hi.txt").data, b"changed")

    def test_share_link_preview_page_and_curl(self):
        """One link: browsers get a preview page, curl/wget get the bytes (so `curl <link> | sh` works)."""
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            script = b"#!/bin/sh\necho hello from ark\n"
            a.upload([("install.sh", script), ("pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50)])
            link = a.call("post", "/api/share", json={"path": "/install.sh", "hours": 1}).get_json()["url"]
            self.assertTrue(link.endswith("/install.sh"), link)
            path = link.replace("http://localhost", "")
            token_only = path.rsplit("/", 1)[0]
            anon = a.anon()
            # curl-style requests: raw bytes, text/plain, inline, same through the bare token
            for p in (path, token_only):
                r = anon.get(p, headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"})
                self.assertEqual(r.data, script)
                self.assertTrue(r.headers["content-type"].startswith("text/plain"), r.headers["content-type"])
                self.assertTrue(r.headers["content-disposition"].startswith("inline"))
            # Accept */* with no user agent (python, fetch, ...) is raw too
            self.assertEqual(anon.get(path).data, script)
            # a browser gets the preview page
            browser = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "User-Agent": "Mozilla/5.0"}
            r = anon.get(path, headers=browser)
            self.assertTrue(r.headers["content-type"].startswith("text/html"))
            page = r.data.decode()
            self.assertIn("install.sh", page)
            self.assertIn("| sh", page)                                  # ready-to-paste run command
            self.assertIn("curl -fLO", page)
            self.assertIn('data-kind="text"', page)
            self.assertIn("/static/share.js", page)
            # ... unless it asks for the raw bytes or a download explicitly
            self.assertEqual(anon.get(path + "?raw=1", headers=browser).data, script)
            d = anon.get(path + "?download=1", headers=browser)
            self.assertEqual(d.data, script)
            self.assertTrue(d.headers["content-disposition"].startswith("attachment"))
            # a tool that happens to send text/html still gets the bytes
            self.assertEqual(anon.get(path, headers={"User-Agent": "curl/8.5.0", "Accept": "text/html"}).data, script)
            # HEAD and ranges work on the raw response
            self.assertEqual(anon.tc.head(path, headers={"User-Agent": "curl/8"}).headers["content-length"], str(len(script)))
            r = anon.get(path, headers={"User-Agent": "curl/8", "Range": "bytes=0-1"})
            self.assertEqual((r.status_code, r.data), (206, b"#!"))
            # images are previewed, and the raw URL serves them with their own type
            img = a.call("post", "/api/share", json={"path": "/pic.png", "hours": 1}).get_json()["url"].replace("http://localhost", "")
            page = anon.get(img, headers=browser).data.decode()
            self.assertIn('data-kind="image"', page)
            self.assertEqual(anon.get(img + "?raw=1").headers["content-type"], "image/png")
            # the page escapes the file name
            self.assertNotIn("<script>alert", page)
            # tampered or expired links stay errors
            self.assertEqual(anon.get(token_only + "x", headers=browser).status_code, 404)

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
            for js in ("files-core.js", "editor-core.js", "ui.js", "editor.js", "settings.js", "progress.js", "app.js", "style.css"):
                self.assertEqual(a.c.get("/static/" + js).status_code, 200, js)
            self.assertEqual(a.c.get("/static/../app.py").status_code, 404)
            self.assertEqual(a.c.get("/healthz").get_json()["ok"], True)
            self.assertEqual(a.c.get("/static/hljs/highlight.bundle.js").status_code, 200)

    def test_clean_urls_serve_the_app(self):
        # /browse/<folder> and /trash replace the old "#/folder" links: a reload or a shared link must open the app, not a 404
        with sandbox() as (st, remote):
            a = Api(st, remote)
            home = a.c.get("/").get_data(as_text=True)
            for url in ("/browse", "/browse/Photos/2024", "/browse/with%20space/%E0%A6%85", "/trash"):
                r = a.c.get(url)
                self.assertEqual(r.status_code, 200, url)
                self.assertEqual(r.get_data(as_text=True), home, url)
            self.assertEqual(a.c.get("/api/trash").status_code, 401)      # the API route of the same name is untouched

    def test_assets_logo_cache_and_gzip(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            html = a.c.get("/").get_data(as_text=True)
            self.assertNotIn("icon.svg", html)
            for png in ("icon-32.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
                r = a.c.get("/static/" + png)
                self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/png"), png)
            self.assertEqual(a.c.get("/favicon.ico").status_code, 200)
            icons = a.c.get("/manifest.webmanifest").get_json()["icons"]
            self.assertTrue(icons and all(i["type"] == "image/png" for i in icons))
            # versioned static URLs are cached for good, unversioned ones are revalidated
            self.assertIn("immutable", a.c.get("/static/app.js?v=1").headers["Cache-Control"])
            self.assertEqual(a.c.get("/static/app.js").headers["Cache-Control"], "no-cache")
            # text is gzipped when the browser accepts it, and is identical once decoded
            raw = a.c.get("/static/app.js", headers={"Accept-Encoding": "identity"})
            z = a.c.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(z.headers.get("Content-Encoding"), "gzip")
            self.assertEqual(z.get_data(), raw.get_data())
            self.assertLess(z.num_bytes_downloaded, len(raw.get_data()) // 2)
            self.assertIsNone(raw.headers.get("Content-Encoding"))
            png = a.c.get("/static/icon-512.png", headers={"Accept-Encoding": "gzip"})
            self.assertIsNone(png.headers.get("Content-Encoding"))      # already compressed: left alone

    def test_trash_ids_for_undo(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            self.assertEqual(a.call("post", "/api/newfile", json={"path": "/u.txt", "content": "x"}).status_code, 200)
            r = a.call("post", "/api/delete", json={"paths": ["/u.txt"]}).get_json()
            tid = r["results"][0]["trash_id"]
            self.assertTrue(tid)
            self.assertTrue(a.call("get", "/api/status").get_json()["trash"])
            self.assertEqual(a.call("post", "/api/trash/restore", json={"ids": [tid]}).status_code, 200)
            names = [e["name"] for e in a.call("get", "/api/list?path=/").get_json()["entries"]]
            self.assertIn("u.txt", names)
            self.assertIsNone(a.call("post", "/api/delete", json={"paths": ["/u.txt"], "permanent": True}).get_json()["results"][0]["trash_id"])


if __name__ == "__main__":
    unittest.main()
