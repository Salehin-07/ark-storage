"""What the async design adds: shared downloads, stale-while-revalidate listings, HEAD, ranges inside encrypted files."""
import asyncio
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from tests.api_harness import Api
from tests.helpers import ApiCase, sandbox


class AsyncTests(ApiCase):
    def test_parallel_readers_share_one_download(self):
        with sandbox(chunk_mb=1) as (st, remote):
            a = Api(st, remote)
            a.login()
            data = os.urandom(3_500_000)
            a.upload([("big.bin", data)])
            a.call("post", "/api/admin/clear-cache", json={})
            calls = []
            orig = a.st.client.download_blob

            async def counted(sha, dest, progress=None):
                calls.append(sha)
                await asyncio.sleep(0.2)             # make the overlap certain
                return await orig(sha, dest, progress)
            a.st.client.download_blob = counted
            with ThreadPoolExecutor(12) as ex:
                got = list(ex.map(lambda _i: a.c.get("/api/raw?path=/big.bin").content, range(12)))
            self.assertTrue(all(g == data for g in got))
            self.assertEqual(len(calls), len(set(calls)), "a piece was downloaded twice")
            self.assertEqual(len(set(calls)), 4)

    def test_stale_listing_is_served_at_once_and_revalidated(self):
        with sandbox(refresh_seconds=10) as (st, remote):
            a = Api(st, remote)
            a.login()
            a.upload([("a.txt", b"1")])
            s = a.st
            s._checked = time.time() - 15                 # stale, but inside the grace window
            seen = []
            orig = s.client.tree

            async def slow(branch):
                seen.append(1)
                await asyncio.sleep(0.5)
                return await orig(branch)
            s.client.tree = slow
            t = time.time()
            r = a.c.get("/api/list?path=/")
            self.assertLess(time.time() - t, 0.4, "the request waited for the revalidation")
            self.assertEqual(r.status_code, 200)
            time.sleep(1.0)
            self.assertEqual(len(seen), 1)                # the background task ran exactly once
            self.assertLess(time.time() - s._checked, 5)

    def test_head_has_no_body_and_range_inside_encrypted_file(self):
        with sandbox(chunk_mb=1, encrypt=True, passphrase="pw") as (st, remote):
            a = Api(st, remote)
            a.login()
            data = os.urandom(5_000_000)
            a.upload([("e.bin", data)])
            h = a.c.tc.head("/api/raw?path=/e.bin")
            self.assertEqual((h.status_code, h.headers["content-length"], h.content), (200, str(len(data)), b""))
            for lo, hi in ((0, 9), (2_097_150, 2_097_160), (4_999_990, 4_999_999), (1_000_000, 4_200_000)):
                r = a.c.get("/api/raw?path=/e.bin", headers={"Range": f"bytes={lo}-{hi}"})
                self.assertEqual((r.status_code, r.content), (206, data[lo:hi + 1]), (lo, hi))
            self.assertEqual(a.c.get("/api/raw?path=/e.bin", headers={"Range": "bytes=9999999-"}).status_code, 416)


if __name__ == "__main__":
    unittest.main()
