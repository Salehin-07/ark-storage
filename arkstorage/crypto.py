"""Streaming file encryption (AES-256-GCM, 1 MiB segments).

Layout of an encrypted file::

    "ARK1" | version(1) | seg_exp(1) | file_salt(16) | segment*

Each segment is ``ciphertext || tag`` of up to 1 MiB of plaintext. The key of a file is
HKDF(master, file_salt); the nonce is the segment counter; the associated data is the header plus a
"final segment" flag, so truncation, reordering and tampering are all detected. Segments have a fixed
size, so any byte range can be decrypted without reading the rest (video seeking, PDF viewers).
The master key is scrypt(passphrase, repo salt).
"""
import io
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"ARK1"
VERSION = 1
SEG_EXP = 20
SEG = 1 << SEG_EXP
TAG = 16
HEADER = 4 + 1 + 1 + 16


class CryptoError(Exception):
    pass


def derive_master(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _subkey(master: bytes, fsalt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=fsalt, info=b"ark-storage file v1").derive(master)


def _nonce(i: int) -> bytes:
    return struct.pack(">IQ", 0, i)


def plain_size(enc_size: int) -> int:
    """Plaintext length of an encrypted file of `enc_size` bytes."""
    body = enc_size - HEADER
    if body < TAG:
        raise CryptoError("not an Ark encrypted file (too short)")
    n = -(-body // (SEG + TAG))
    return body - n * TAG


def encrypted_size(plain: int) -> int:
    n = max(1, -(-plain // SEG))
    return HEADER + plain + n * TAG


class Encryptor:
    """Incremental encryption: ``header`` once, then ``seg(plain, final)`` for each segment in order."""

    def __init__(self, master: bytes):
        fsalt = os.urandom(16)
        self.header = MAGIC + bytes([VERSION, SEG_EXP]) + fsalt
        self._aes = AESGCM(_subkey(master, fsalt))
        self._i = 0

    def seg(self, plain: bytes, final: bool) -> bytes:
        ct = self._aes.encrypt(_nonce(self._i), plain, self.header + (b"\x01" if final else b"\x00"))
        self._i += 1
        return ct


def encrypt_stream(master: bytes, src, dst) -> int:
    """Encrypt file object `src` into `dst`; returns bytes written."""
    enc = Encryptor(master)
    dst.write(enc.header)
    written = len(enc.header)
    cur = src.read(SEG)
    while True:
        nxt = src.read(SEG) if len(cur) == SEG else b""
        ct = enc.seg(cur, not nxt)
        dst.write(ct)
        written += len(ct)
        if not nxt:
            return written
        cur = nxt


def decrypt_range(master: bytes, read_at, enc_size: int, start: int, end: int):
    """Yield plaintext bytes [start, end). `read_at(offset, n)` reads ciphertext."""
    total = plain_size(enc_size)
    end = min(end, total)
    header = read_at(0, HEADER)
    if len(header) != HEADER or header[:4] != MAGIC:
        raise CryptoError("not an Ark encrypted file")
    aes = AESGCM(_subkey(master, header[6:22]))
    nseg = max(1, -(-total // SEG))
    if start >= end:
        if total == 0:        # still authenticate an empty file
            _open_seg(aes, header, read_at, 0, 0, True)
        return
    for i in range(start // SEG, (end - 1) // SEG + 1):
        plen = min(SEG, total - i * SEG)
        pt = _open_seg(aes, header, read_at, i, plen, i == nseg - 1)
        lo = max(start, i * SEG) - i * SEG
        hi = min(end, i * SEG + plen) - i * SEG
        yield pt[lo:hi]


def stored_span(enc_size: int, start: int, end: int):
    """Ciphertext byte ranges ``[(a, b), ...]`` that ``decrypt_range`` will read for plaintext [start, end).

    Lets an async caller make sure the right stored pieces are on disk before decrypting in a worker thread.
    """
    total = plain_size(enc_size)
    end = min(end, total)
    first = start // SEG if start < end else 0
    last = (end - 1) // SEG if start < end else 0
    lo = HEADER + first * (SEG + TAG)
    hi = min(enc_size, HEADER + (last + 1) * (SEG + TAG))
    return [(0, HEADER), (lo, hi)]


def _open_seg(aes, header, read_at, i, plen, final):
    off = HEADER + i * (SEG + TAG)
    ct = read_at(off, plen + TAG)
    if len(ct) != plen + TAG:
        raise CryptoError("encrypted file is truncated")
    try:
        return aes.decrypt(_nonce(i), ct, header + (b"\x01" if final else b"\x00"))
    except InvalidTag:
        raise CryptoError("wrong passphrase or the file was modified") from None


def encrypt_bytes(master: bytes, data: bytes) -> bytes:
    out = io.BytesIO()
    encrypt_stream(master, io.BytesIO(data), out)
    return out.getvalue()


def decrypt_bytes(master: bytes, blob: bytes) -> bytes:
    return b"".join(decrypt_range(master, lambda o, n: blob[o:o + n], len(blob), 0, plain_size(len(blob))))
