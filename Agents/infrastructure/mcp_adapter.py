from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

from Agents.application.scenario_models import AuthorityRetrievalRequest
from Agents.services.tool_hub import (
    AuthorityDocument,
    AuthorityToolResult,
    ToolExecutionError,
)
from utils.pkulaw_mcp_client import McpHttpError, call_service_query, parse_mcp_result


class PkulawAuthoritySearchAdapter:
    """Bounded async adapter around the provider's synchronous MCP client."""

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_seconds: float = 60,
        max_attempts: int = 3,
        query_callable: Callable[..., Any] = call_service_query,
    ) -> None:
        self._token = (token or os.getenv("PKULAW_MCP_TOKEN") or "").strip()
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._query_callable = query_callable
        if not self._token:
            raise RuntimeError("PKULAW_MCP_TOKEN is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

    async def search(
        self,
        request: AuthorityRetrievalRequest,
    ) -> AuthorityToolResult:
        normalized_query = " ".join(request.query.split())
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._query_callable,
                        request.tool_name,
                        normalized_query,
                        self._token,
                    ),
                    timeout=self._timeout_seconds,
                )
                parsed = parse_mcp_result(raw)
                if parsed["is_error"]:
                    raise ToolExecutionError(
                        "authority provider returned a protocol error",
                        retryable=False,
                    )
                documents = self._normalize_documents(parsed["items"])
                return AuthorityToolResult(
                    tool_name=request.tool_name,
                    normalized_query=normalized_query,
                    attempts=attempt,
                    documents=documents,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retryable = self._is_retryable(exc)
                if not retryable or attempt == self._max_attempts:
                    if isinstance(exc, ToolExecutionError):
                        raise
                    raise ToolExecutionError(
                        f"authority retrieval failed: {exc}",
                        retryable=retryable,
                    ) from exc
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    def _normalize_documents(self, items: list[Any]) -> tuple[AuthorityDocument, ...]:
        documents: list[AuthorityDocument] = []
        pending_title = ""
        for item in items:
            if isinstance(item, dict):
                canonical = json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                title = self._first(item, "title", "name", "case_name", "law_name")
                source_url = self._first(item, "url", "link", "href", "doc_url")
                source_id = self._first(item, "id", "source_id", "doc_id") or source_url
                documents.append(
                    self._document(
                        source_id=source_id,
                        title=title,
                        source_url=source_url,
                        excerpt=self._first(
                            item,
                            "article",
                            "summary",
                            "snippet",
                            "abstract",
                            "content",
                            "text",
                        )
                        or canonical,
                    )
                )
                continue
            text = str(item).strip()
            if not text:
                continue
            if text.startswith(("http://", "https://")):
                documents.append(
                    self._document(
                        source_id=text,
                        title=pending_title,
                        source_url=text,
                        excerpt=pending_title or text,
                    )
                )
                pending_title = ""
            elif pending_title:
                documents.append(
                    self._document(
                        source_id="",
                        title=pending_title,
                        source_url="",
                        excerpt=f"{pending_title}\n{text}",
                    )
                )
                pending_title = ""
            else:
                pending_title = text
        if pending_title:
            documents.append(
                self._document(
                    source_id="",
                    title=pending_title,
                    source_url="",
                    excerpt=pending_title,
                )
            )
        return tuple(documents)

    def _document(
        self,
        *,
        source_id: str,
        title: str,
        source_url: str,
        excerpt: str,
    ) -> AuthorityDocument:
        content_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        return AuthorityDocument(
            source_id=source_id or f"sha256:{content_hash}",
            title=title,
            source_url=source_url,
            excerpt=excerpt,
            content_hash=content_hash,
        )

    def _is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, ToolExecutionError):
            return exc.retryable
        if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError)):
            return True
        if isinstance(exc, McpHttpError):
            return exc.status_code == 429 or 500 <= exc.status_code <= 599
        return False

    def _first(self, item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""
