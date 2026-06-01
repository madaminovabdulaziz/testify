"""Filter: passes only when a message carries a photo (PRODUCT_BLUEPRINT §8.2 step 4)."""

from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message


class PhotoOnly(Filter):
    """True iff ``message.photo`` is populated (i.e. the user sent an image)."""

    async def __call__(self, message: Message) -> bool:
        return bool(message.photo)
