"""Unit tests for :class:`app.services.image_hasher.ImageHasher`.

We synthesize test images at runtime via Pillow rather than committing
PNG fixtures — the suite stays self-contained and we control exactly how
visually similar / different the inputs are.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.services.image_hasher import ImageHasher

# ---------- image builders ----------


def _png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _gradient_image() -> Image.Image:
    """A 128×128 image with a smooth horizontal gradient — pHash sees clear structure."""
    img = Image.new("RGB", (128, 128))
    pixels = img.load()
    assert pixels is not None
    for x in range(128):
        for y in range(128):
            pixels[x, y] = (x * 2, y * 2, (x + y) % 256)
    return img


def _gradient_with_small_dot() -> Image.Image:
    """The gradient + a tiny black dot in one corner — pHash should stay close."""
    img = _gradient_image()
    draw = ImageDraw.Draw(img)
    draw.rectangle((2, 2, 4, 4), fill=(0, 0, 0))
    return img


def _checkerboard_image() -> Image.Image:
    """A 128×128 black/white checkerboard — visually unrelated to the gradient."""
    img = Image.new("RGB", (128, 128), "white")
    pixels = img.load()
    assert pixels is not None
    for x in range(128):
        for y in range(128):
            if ((x // 8) + (y // 8)) % 2 == 0:
                pixels[x, y] = (0, 0, 0)
    return img


# ---------- hash() ----------


def test_hash_is_deterministic_for_identical_bytes() -> None:
    hasher = ImageHasher()
    payload = _png_bytes(_gradient_image())
    assert hasher.hash(payload) == hasher.hash(payload)


def test_hash_fits_in_signed_64_bit_range() -> None:
    """The returned value must round-trip through ``asyncmy`` — i.e. fit
    in a signed 64-bit ``BIGINT`` column. The bit pattern still covers
    the full 64-bit pHash range; we just sign-extend the top bit."""
    hasher = ImageHasher()
    value = hasher.hash(_png_bytes(_gradient_image()))
    assert -(1 << 63) <= value < (1 << 63)


def test_hash_raises_value_error_on_non_image_bytes() -> None:
    with pytest.raises(ValueError):
        ImageHasher().hash(b"not an image at all")


# ---------- is_similar(): integer math ----------


def test_is_similar_identical_hashes() -> None:
    hasher = ImageHasher()
    assert hasher.is_similar(0xDEADBEEFCAFEBABE, 0xDEADBEEFCAFEBABE) is True


def test_is_similar_within_threshold() -> None:
    hasher = ImageHasher()
    # Difference of 3 bits (0b111) is well under the default threshold of 5.
    assert hasher.is_similar(0xFF, 0xF8) is True
    # Difference of 6 bits is *over* the default threshold of 5.
    assert hasher.is_similar(0xFF, 0xC0) is False


def test_is_similar_respects_custom_threshold() -> None:
    hasher = ImageHasher()
    # 8 differing bits → True only when threshold ≥ 8.
    a, b = 0x00, 0xFF
    assert hasher.is_similar(a, b, threshold=7) is False
    assert hasher.is_similar(a, b, threshold=8) is True


def test_is_similar_negative_threshold_raises() -> None:
    with pytest.raises(ValueError):
        ImageHasher().is_similar(0, 0, threshold=-1)


# ---------- end-to-end similarity behavior ----------


def test_slightly_modified_image_stays_within_threshold() -> None:
    hasher = ImageHasher()
    base = hasher.hash(_png_bytes(_gradient_image()))
    nudged = hasher.hash(_png_bytes(_gradient_with_small_dot()))
    # Threshold of 10 leaves headroom for pHash's variability on a small
    # local change while still being well under "completely different".
    assert hasher.is_similar(base, nudged, threshold=10)


def test_visually_different_images_exceed_default_threshold() -> None:
    hasher = ImageHasher()
    gradient = hasher.hash(_png_bytes(_gradient_image()))
    checker = hasher.hash(_png_bytes(_checkerboard_image()))
    assert not hasher.is_similar(gradient, checker)  # default threshold 5
