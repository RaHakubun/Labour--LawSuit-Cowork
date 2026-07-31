from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from Agents.application.scenario_models import AuthorityRetrievalRequest


class ToolExecutionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class AuthorityDocument:
    source_id: str
    title: str
    source_url: str
    excerpt: str
    content_hash: str


@dataclass(frozen=True)
class AuthorityToolResult:
    tool_name: str
    normalized_query: str
    attempts: int
    documents: tuple[AuthorityDocument, ...]


class AuthoritySearchAdapter(Protocol):
    async def search(
        self,
        request: AuthorityRetrievalRequest,
    ) -> AuthorityToolResult: ...


class ToolHub:
    """Single asynchronous entrypoint for external authority retrieval."""

    def __init__(self, authority_search: AuthoritySearchAdapter) -> None:
        self._authority_search = authority_search

    async def retrieve_authorities(
        self,
        request: AuthorityRetrievalRequest,
    ) -> AuthorityToolResult:
        result = await self._authority_search.search(request)
        if result.tool_name != request.tool_name:
            raise ToolExecutionError(
                "authority adapter returned a mismatched tool name",
                retryable=False,
            )
        if result.normalized_query != " ".join(request.query.split()):
            raise ToolExecutionError(
                "authority adapter returned a mismatched normalized query",
                retryable=False,
            )
        if request.required and not result.documents:
            raise ToolExecutionError(
                "required authority retrieval returned no sources",
                retryable=False,
            )
        return result
