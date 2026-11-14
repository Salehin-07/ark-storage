import os
import unittest

from arkstorage import crypto as c


class CryptoTests(unittest.TestCase):
    key = c.derive_master("pass", b"s" * 16)

    def test_roundtrip_sizes(self):
        for n in (0, 1, c.SEG - 1, c.SEG, c.SEG + 1, 3 * c.SEG + 5):
            data = os.urandom(n)
            blob = c.encrypt_bytes(self.key, data)
            self.assertEqual(len(blob), c.encrypted_size(n))
            self.assertEqual(c.plain_size(len(blob)), n)
            self.assertEqual(c.decrypt_bytes(self.key, blob), data)

    def test_range_reads(self):
        data = os.urandom(3 * c.SEG + 123)
        blob = c.encrypt_bytes(self.key, data)
        rd = lambda o, n: blob[o:o + n]  # noqa: E731
        for a, b in ((0, 10), (c.SEG - 3, c.SEG + 3), (2 * c.SEG, len(data)), (5, 5)):
            self.assertEqual(b"".join(c.decrypt_range(self.key, rd, len(blob), a, b)), data[a:b])

    def test_wrong_key_and_tampering_fail(self):
        blob = c.encrypt_bytes(self.key, b"secret" * 100)
        with self.assertRaises(c.CryptoError):
            c.decrypt_bytes(c.derive_master("other", b"s" * 16), blob)
        bad = bytearray(blob)
        bad[-1] ^= 1
        with self.assertRaises(c.CryptoError):
            c.decrypt_bytes(self.key, bytes(bad))

    def test_truncation_detected(self):
        data = os.urandom(2 * c.SEG)
        blob = c.encrypt_bytes(self.key, data)
        cut = blob[:c.HEADER + c.SEG + c.TAG]              # drop the final segment
        with self.assertRaises(c.CryptoError):
            c.decrypt_bytes(self.key, cut)

    def test_two_encryptions_differ(self):
        self.assertNotEqual(c.encrypt_bytes(self.key, b"x"), c.encrypt_bytes(self.key, b"x"))


if __name__ == "__main__":
    unittest.main()
