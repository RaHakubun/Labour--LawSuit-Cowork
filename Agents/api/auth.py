from __future__ import annotations

import hmac
import json
import os

from fastapi import Header, HTTPException


class BearerTokenAuthenticator:
    def __init__(self, token_actors: dict[str, str]) -> None:
        normalized = {
            token.strip(): actor.strip()
            for token, actor in token_actors.items()
            if token.strip() and actor.strip()
        }
        if not normalized:
            raise RuntimeError("at least one API bearer token is required")
        self._token_actors = normalized

    @classmethod
    def from_environment(cls) -> "BearerTokenAuthenticator":
        raw = os.getenv("APP_API_TOKENS_JSON", "").strip()
        if not raw:
            raise RuntimeError("APP_API_TOKENS_JSON is required")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("APP_API_TOKENS_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("APP_API_TOKENS_JSON must map bearer tokens to actor IDs")
        return cls({str(token): str(actor) for token, actor in payload.items()})

    async def actor_id(
        self,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> str:
        if authorization is None:
            raise HTTPException(status_code=401, detail="Bearer token is required")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="Bearer token is required")
        supplied = token.strip()
        for expected, actor_id in self._token_actors.items():
            if hmac.compare_digest(supplied, expected):
                return actor_id
        raise HTTPException(status_code=401, detail="invalid bearer token")
