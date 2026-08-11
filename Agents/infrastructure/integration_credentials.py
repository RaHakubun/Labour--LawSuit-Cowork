from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import secrets
from typing import Literal, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from Agents.domain.case_state import StrictModel, utc_now
from Agents.domain.errors import IntegrationNotConfiguredError

from .database import IntegrationCredentialRow


IntegrationProvider = Literal["llm", "ocr", "mcp"]
INTEGRATION_PROVIDERS: tuple[IntegrationProvider, ...] = ("llm", "ocr", "mcp")


class IntegrationCredentialInput(StrictModel):
    endpoint: HttpUrl | None = None
    model: str | None = Field(default=None, min_length=1, max_length=500)
    secret: str | None = Field(default=None, min_length=1, max_length=10000)


class ResolvedIntegrationCredential(StrictModel):
    provider: IntegrationProvider
    endpoint: str | None = None
    model: str | None = None
    secret: str


class IntegrationStatus(StrictModel):
    provider: IntegrationProvider
    configured: bool
    endpoint: str | None = None
    model: str | None = None
    updated_at: str | None = None


class CredentialCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("integration master key must be exactly 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, provider: IntegrationProvider, secret: str) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        encrypted = self._cipher.encrypt(
            nonce,
            secret.encode("utf-8"),
            provider.encode("ascii"),
        )
        return encrypted, nonce

    def decrypt(
        self,
        provider: IntegrationProvider,
        encrypted: bytes,
        nonce: bytes,
    ) -> str:
        plaintext = self._cipher.decrypt(
            nonce,
            encrypted,
            provider.encode("ascii"),
        )
        return plaintext.decode("utf-8")


def load_or_create_credential_cipher(path: Path | None = None) -> CredentialCipher:
    key_path = path or Path(
        os.getenv(
            "INTEGRATION_MASTER_KEY_FILE",
            str(Path(__file__).resolve().parents[2] / ".runtime" / "integration-master-key"),
        )
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        encoded = key_path.read_bytes().strip()
    except FileNotFoundError:
        encoded = base64.urlsafe_b64encode(secrets.token_bytes(32))
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            encoded = key_path.read_bytes().strip()
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded + b"\n")
    os.chmod(key_path, 0o600)
    try:
        key = base64.urlsafe_b64decode(encoded)
        return CredentialCipher(key)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("integration master key file is invalid") from exc


class PostgresIntegrationCredentialStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cipher: CredentialCipher,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = cipher

    async def upsert(
        self,
        provider: IntegrationProvider,
        value: IntegrationCredentialInput,
    ) -> IntegrationStatus:
        endpoint = str(value.endpoint).rstrip("/") if value.endpoint else None
        now = utc_now()
        async with self._session_factory.begin() as session:
            row = await session.scalar(
                select(IntegrationCredentialRow)
                .where(IntegrationCredentialRow.provider == provider)
                .with_for_update()
            )
            self._validate(provider, value, configured=row is not None)
            if value.secret is None:
                if row is None:
                    raise ValueError(f"{provider} requires a secret")
                encrypted = bytes(row.encrypted_secret)
                nonce = bytes(row.nonce)
            else:
                encrypted, nonce = self._cipher.encrypt(provider, value.secret)
            if row is None:
                row = IntegrationCredentialRow(
                    provider=provider,
                    endpoint=endpoint,
                    model=value.model,
                    encrypted_secret=encrypted,
                    nonce=nonce,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.endpoint = endpoint
                row.model = value.model
                row.encrypted_secret = encrypted
                row.nonce = nonce
                row.updated_at = now
        return IntegrationStatus(
            provider=provider,
            configured=True,
            endpoint=endpoint,
            model=value.model,
            updated_at=now.isoformat(),
        )

    async def require(
        self,
        provider: IntegrationProvider,
    ) -> ResolvedIntegrationCredential:
        async with self._session_factory() as session:
            row = await session.get(IntegrationCredentialRow, provider)
            if row is None:
                raise IntegrationNotConfiguredError(provider)
            secret = self._cipher.decrypt(
                provider,
                bytes(row.encrypted_secret),
                bytes(row.nonce),
            )
            return ResolvedIntegrationCredential(
                provider=provider,
                endpoint=row.endpoint,
                model=row.model,
                secret=secret,
            )

    async def list_statuses(self) -> list[IntegrationStatus]:
        async with self._session_factory() as session:
            rows = {
                cast(IntegrationProvider, row.provider): row
                for row in (await session.scalars(select(IntegrationCredentialRow))).all()
            }
        return [
            IntegrationStatus(
                provider=provider,
                configured=provider in rows,
                endpoint=rows[provider].endpoint if provider in rows else None,
                model=rows[provider].model if provider in rows else None,
                updated_at=(rows[provider].updated_at.isoformat() if provider in rows else None),
            )
            for provider in INTEGRATION_PROVIDERS
        ]

    async def delete(self, provider: IntegrationProvider) -> bool:
        async with self._session_factory.begin() as session:
            row = await session.get(IntegrationCredentialRow, provider)
            if row is None:
                return False
            await session.delete(row)
            return True

    def _validate(
        self,
        provider: IntegrationProvider,
        value: IntegrationCredentialInput,
        *,
        configured: bool,
    ) -> None:
        if value.model is not None and not value.model.strip():
            raise ValueError(f"{provider} model must not be blank")
        if value.secret is not None and not value.secret.strip():
            raise ValueError(f"{provider} secret must not be blank")
        if provider in {"llm", "ocr"} and (value.endpoint is None or not value.model):
            raise ValueError(f"{provider} requires endpoint and model")
        if provider == "mcp" and (value.endpoint is not None or value.model is not None):
            raise ValueError("mcp accepts only a token")
        if not configured and value.secret is None:
            raise ValueError(f"{provider} requires a secret")
