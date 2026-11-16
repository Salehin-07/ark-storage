import shutil
import unittest
from pathlib import Path

from arkstorage import office
from arkstorage.app import kind_of, mime_of
from tests.helpers import sandbox
from tests.test_api import Api

DOC = Path("/tmp/samples/report.doc")


class OfficeTests(unittest.TestCase):
    def test_kinds_and_mimes(self):
        for n in ("a.docx", "a.DOC", "b.xlsx", "b.xls", "c.pptx", "c.ppt", "d.odt", "e.ods", "f.rtf"):
            self.assertEqual(kind_of(n), "office", n)
        self.assertEqual(kind_of("a.txt"), "text")
        self.assertIn("wordprocessingml", mime_of("a.docx"))

    def test_api_flags_warm_and_rules(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            self.assertIn("office_convert", a.c.get("/api/session").get_json())
            a.upload([("w.docx", b"PK\x03\x04 not really a docx"), ("n.txt", b"hello")])
            self.assertEqual(a.call("post", "/api/warm", json={"path": "/w.docx"}).status_code, 204)
            self.assertEqual(a.call("post", "/api/warm", json={"path": "/nope.docx"}).status_code, 404)
            # docx/xlsx/pptx are drawn by the browser: the server-side converter refuses them
            self.assertEqual(a.c.get("/api/preview-pdf?path=/w.docx").status_code, 400)
            self.assertEqual(a.c.get("/api/preview-pdf?path=/n.txt").status_code, 400)
            # office files are not editable as text
            self.assertEqual(a.c.get("/api/text?path=/w.docx").status_code, 415)
            self.assertEqual(a.c.get("/api/list?path=/").get_json()["entries"][0]["kind"] in ("office", "text"), True)

    @unittest.skipUnless(office.available() and DOC.exists(), "LibreOffice and a sample .doc needed")
    def test_legacy_doc_converts_to_pdf_and_is_cached(self):
        with sandbox() as (st, remote):
            a = Api(st, remote)
            a.login()
            a.upload([("old.doc", DOC.read_bytes())])
            r = a.c.get("/api/preview-pdf?path=/old.doc")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.data.startswith(b"%PDF"))
            self.assertEqual(a.c.get("/api/preview-pdf?path=/old.doc", headers={"Range": "bytes=0-3"}).status_code, 206)
            self.assertTrue(list((Path(st.cache.dir).parent / "office").glob("*.pdf")))

    def test_without_libreoffice_is_a_clear_error(self):
        real = shutil.which
        try:
            shutil.which = lambda *_a, **_k: None
            with sandbox() as (st, remote):
                a = Api(st, remote)
                a.login()
                a.upload([("old.doc", b"\xd0\xcf\x11\xe0 fake")])
                r = a.c.get("/api/preview-pdf?path=/old.doc")
                self.assertEqual(r.status_code, 501)
                self.assertIn("LibreOffice", r.get_json()["error"])
        finally:
            shutil.which = real


if __name__ == "__main__":
    unittest.main()
