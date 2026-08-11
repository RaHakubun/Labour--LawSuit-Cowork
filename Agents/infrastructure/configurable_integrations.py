from __future__ import annotations

from collections.abc import Sequence

from Agents.application.decisions import ControllerDecision
from Agents.application.legal_models import DocumentDraftResult, LegalAnalysisResult
from Agents.application.scenario_models import AuthorityRetrievalRequest, ScenarioResult
from Agents.domain.case_state import CaseState
from Agents.services.ocr import OcrResult
from Agents.services.tool_hub import AuthorityToolResult

from .integration_credentials import PostgresIntegrationCredentialStore
from .llm_adapter import (
    AsyncOpenAIControllerDecisionProvider,
    AsyncOpenAILegalResultProvider,
    AsyncOpenAIScenarioResultProvider,
)
from .mcp_adapter import PkulawAuthoritySearchAdapter
from .vision_ocr_adapter import OpenAIVisionOcrAdapter


class DatabaseBackedControllerDecisionProvider:
    def __init__(self, store: PostgresIntegrationCredentialStore) -> None:
        self._store = store

    async def decide(
        self,
        *,
        case_state: CaseState,
        user_input: str,
    ) -> ControllerDecision:
        value = await self._store.require("llm")
        provider = AsyncOpenAIControllerDecisionProvider(
            base_url=value.endpoint,
            api_key=value.secret,
            model=value.model,
        )
        return await provider.decide(case_state=case_state, user_input=user_input)


class DatabaseBackedScenarioResultProvider:
    def __init__(self, store: PostgresIntegrationCredentialStore) -> None:
        self._store = store

    async def analyze(
        self,
        *,
        role_id: str,
        scene_id: str,
        case_state: CaseState,
    ) -> ScenarioResult:
        value = await self._store.require("llm")
        provider = AsyncOpenAIScenarioResultProvider(
            base_url=value.endpoint,
            api_key=value.secret,
            model=value.model,
        )
        return await provider.analyze(
            role_id=role_id,
            scene_id=scene_id,
            case_state=case_state,
        )


class DatabaseBackedLegalResultProvider:
    def __init__(self, store: PostgresIntegrationCredentialStore) -> None:
        self._store = store

    async def analyze(self, *, context: dict[str, object]) -> LegalAnalysisResult:
        provider = await self._provider()
        return await provider.analyze(context=context)

    async def draft_document(
        self,
        *,
        context: dict[str, object],
        document_type: str,
    ) -> DocumentDraftResult:
        provider = await self._provider()
        return await provider.draft_document(
            context=context,
            document_type=document_type,
        )

    async def _provider(self) -> AsyncOpenAILegalResultProvider:
        value = await self._store.require("llm")
        return AsyncOpenAILegalResultProvider(
            base_url=value.endpoint,
            api_key=value.secret,
            model=value.model,
        )


class DatabaseBackedAuthoritySearchAdapter:
    def __init__(self, store: PostgresIntegrationCredentialStore) -> None:
        self._store = store

    async def search(
        self,
        request: AuthorityRetrievalRequest,
    ) -> AuthorityToolResult:
        value = await self._store.require("mcp")
        return await PkulawAuthoritySearchAdapter(token=value.secret).search(request)


class DatabaseBackedVisionOcrAdapter:
    def __init__(self, store: PostgresIntegrationCredentialStore) -> None:
        self._store = store

    async def recognize(
        self,
        *,
        images: Sequence[bytes],
        media_type: str,
    ) -> OcrResult:
        value = await self._store.require("ocr")
        provider = OpenAIVisionOcrAdapter(
            base_url=value.endpoint,
            api_key=value.secret,
            model=value.model,
        )
        return await provider.recognize(images=images, media_type=media_type)
