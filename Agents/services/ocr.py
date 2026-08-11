from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class OcrResult:
    text: str
    provider: str
    page_count: int


class VisionOcrAdapter(Protocol):
    async def recognize(
        self,
        *,
        images: Sequence[bytes],
        media_type: str,
    ) -> OcrResult: ...
