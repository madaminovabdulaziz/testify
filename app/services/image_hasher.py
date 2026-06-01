"""Perceptual image hashing for receipt deduplication (PRODUCT_BLUEPRINT §14.1).

We use the 64-bit pHash from ``imagehash``. The hash is stored as a
``BIGINT UNSIGNED`` (DATABASE_SPEC §5.3, ``payment_receipts.image_phash``),
and approximate equality is decided by Hamming distance — a XOR followed
by a popcount, both ``O(1)`` on a 64-bit value. For v1 the corpus is
small enough that a linear scan of approved hashes is fine; the upgrade
path (multi-index chunks / BK-tree) is described in DATABASE_SPEC §11.5.
"""

from __future__ import annotations

from io import BytesIO
from typing import Final

import imagehash
from PIL import Image

# Default similarity threshold per ARCHITECTURE_SPEC §8.8 and DATABASE_SPEC §10.3.
DEFAULT_HAMMING_THRESHOLD: Final[int] = 5

# Mask used to coerce the imagehash int into a 64-bit unsigned range.
# pHash is exactly 64 bits, but the masking guards against future config
# changes (e.g. a 128-bit hash variant) silently overflowing the DB column.
_UINT64_MASK: Final[int] = (1 << 64) - 1


class ImageHasher:
    """Stateless wrapper around ``imagehash.phash``. Safe to instantiate once and reuse."""

    def hash(self, image_bytes: bytes) -> int:
        """Compute the 64-bit perceptual hash of an image as a signed integer.

        The return is the **signed-64-bit reinterpretation** of the 64-bit
        pHash bit pattern (i.e. ``v - 2**64`` when the top bit is set).
        We store signed so MySQL's ``BIGINT`` column round-trips through
        ``asyncmy`` without ``OverflowError`` — asyncmy's literal escaper
        rejects Python ints larger than ``sys.maxsize`` (2^63 - 1).
        The bit pattern is preserved, so :meth:`is_similar` still computes
        Hamming distance correctly.

        Raises ``ValueError`` if the bytes don't decode as an image —
        callers (typically :class:`ReceiptService`) should treat that as
        a malformed-upload error and surface a user-friendly message,
        *not* a 500.
        """
        try:
            with Image.open(BytesIO(image_bytes)) as img:
                # ``imagehash.phash`` defaults to ``hash_size=8`` → 8×8 = 64 bits.
                # We force conversion to a flat RGB to avoid surprises on
                # palette / animated / CMYK inputs.
                phash = imagehash.phash(img.convert("RGB"))
        except Exception as exc:
            raise ValueError("Could not decode image for hashing.") from exc

        # ``imagehash.ImageHash`` stringifies to hex.
        unsigned = int(str(phash), 16) & _UINT64_MASK
        return _to_signed_64(unsigned)

    def is_similar(
        self,
        a: int,
        b: int,
        threshold: int = DEFAULT_HAMMING_THRESHOLD,
    ) -> bool:
        """True iff the Hamming distance between two 64-bit hashes is ``<= threshold``.

        Accepts either signed or unsigned representations — the AND with
        the 64-bit mask normalizes both into the same bit pattern before
        ``bit_count``.
        """
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        # ``int.bit_count()`` lands in Python 3.10+; faster than ``bin().count('1')``.
        return ((a ^ b) & _UINT64_MASK).bit_count() <= threshold


def _to_signed_64(unsigned: int) -> int:
    """Reinterpret a 64-bit unsigned integer as its signed-64-bit value."""
    if unsigned >= (1 << 63):
        return unsigned - (1 << 64)
    return unsigned
