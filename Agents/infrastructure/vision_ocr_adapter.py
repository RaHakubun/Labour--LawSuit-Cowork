from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Sequence
from typing import Any

from Agents.services.ocr import OcrResult
from Agents.services.tool_hub import ToolExecutionError


class OpenAIVisionOcrAdapter:
    """Independent OpenAI-compatible vision OCR boundary."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
    ) -> None:
        self._base_url = (base_url or os.getenv("OCR_BASE_URL") or "").strip()
        self._api_key = (api_key or os.getenv("OCR_API_KEY") or "").strip()
        self._model = (model or os.getenv("OCR_MODEL") or "").strip()
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        if not self._base_url:
            raise RuntimeError("OCR_BASE_URL is required")
        if not self._api_key:
            raise RuntimeError("OCR_API_KEY is required")
        if not self._model:
            raise RuntimeError("OCR_MODEL is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

    async def recognize(
        self,
        *,
        images: Sequence[bytes],
        media_type: str,
    ) -> OcrResult:
        if not images:
            raise ValueError("visual OCR requires at least one image")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("Install the openai dependency to run visual OCR") from exc
        content: Any = [
            {
                "type": "text",
                "text": (
                    "逐页忠实识别劳动争议证据中的全部可见文字。保持原有顺序和换行；"
                    "不要总结、纠正、推断或补写看不清的内容。无法识别处标记为[无法识别]。"
                ),
            }
        ]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                }
            )
        client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout_seconds,
        )
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=[{"role": "user", "content": content}],
                )
                text = response.choices[0].message.content or ""
                if not text.strip():
                    raise ToolExecutionError(
                        "visual OCR provider returned empty text",
                        retryable=False,
                    )
                return OcrResult(
                    text=text.strip(),
                    provider=f"openai-compatible:{self._model}",
                    page_count=len(images),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retryable = self._is_retryable(exc)
                if not retryable or attempt == self._max_attempts:
                    if isinstance(exc, ToolExecutionError):
                        raise
                    raise ToolExecutionError(
                        f"visual OCR failed: {exc}",
                        retryable=retryable,
                    ) from exc
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    def _is_retryable(self, error: Exception) -> bool:
        if isinstance(error, ToolExecutionError):
            return error.retryable
        status_code = getattr(error, "status_code", None)
        if status_code == 429 or (
            isinstance(status_code, int) and 500 <= status_code <= 599
        ):
            return True
        return isinstance(error, (TimeoutError, ConnectionError, asyncio.TimeoutError))
