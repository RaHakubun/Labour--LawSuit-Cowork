from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .conversation_contract import now_utc_iso


FACT_STATUSES = {
    "confirmed",
    "user_claimed",
    "pending_verification",
    "inferred",
    "disputed",
}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


@dataclass
class FactItem:
    fact_id: str
    value: Any
    status: str
    source: dict[str, Any] = field(default_factory=dict)
    confidence: str = ""
    updated_at: str = field(default_factory=now_utc_iso)

    def __post_init__(self) -> None:
        if self.status not in FACT_STATUSES:
            allowed = ", ".join(sorted(FACT_STATUSES))
            raise ValueError(f"invalid fact status: {self.status}. allowed: {allowed}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "value": self.value,
            "status": self.status,
            "source": dict(self.source),
            "confidence": self.confidence,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactItem":
        return cls(
            fact_id=str(data["fact_id"]),
            value=data.get("value"),
            status=str(data.get("status", "pending_verification")),
            source=_dict(data.get("source")),
            confidence=str(data.get("confidence", "")),
            updated_at=str(data.get("updated_at", now_utc_iso())),
        )


@dataclass
class EvidenceItem:
    evidence_id: str
    type: str
    source: dict[str, Any] = field(default_factory=dict)
    extracted_text: str = ""
    linked_fact_ids: list[str] = field(default_factory=list)
    probative_value: str = ""
    authenticity_risk: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "type": self.type,
            "source": dict(self.source),
            "extracted_text": self.extracted_text,
            "linked_fact_ids": list(self.linked_fact_ids),
            "probative_value": self.probative_value,
            "authenticity_risk": self.authenticity_risk,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        return cls(
            evidence_id=str(data["evidence_id"]),
            type=str(data.get("type", "")),
            source=_dict(data.get("source")),
            extracted_text=str(data.get("extracted_text", "")),
            linked_fact_ids=[str(item) for item in _list(data.get("linked_fact_ids"))],
            probative_value=str(data.get("probative_value", "")),
            authenticity_risk=str(data.get("authenticity_risk", "")),
        )


@dataclass
class OutputArtifact:
    artifact_id: str
    type: str
    title: str
    content: str
    generated_by: str
    generated_at: str
    case_version: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "generated_by": self.generated_by,
            "generated_at": self.generated_at,
            "case_version": int(self.case_version),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutputArtifact":
        return cls(
            artifact_id=str(data["artifact_id"]),
            type=str(data.get("type", "")),
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            generated_by=str(data.get("generated_by", "")),
            generated_at=str(data.get("generated_at", now_utc_iso())),
            case_version=int(data.get("case_version", 0)),
            metadata=_dict(data.get("metadata")),
        )


@dataclass
class CaseState:
    interaction: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls) -> "CaseState":
        return cls(
            interaction={
                "stage": "controller",
                "user_role": "",
                "current_goal": "",
                "pending_questions": [],
                "confirmed_key_facts": False,
                "blocked_on": "",
            },
            facts={
                "items": {},
                "disputed_facts": [],
            },
            evidence={"items": {}},
            analysis={
                "issues": [],
                "legal_sources": [],
                "calculations": [],
                "preliminary_conclusions": [],
                "missing_information": [],
            },
            outputs={"artifacts": []},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction": dict(self.interaction),
            "facts": {
                "items": dict(self.facts.get("items", {})),
                "disputed_facts": list(self.facts.get("disputed_facts", [])),
            },
            "evidence": {"items": dict(self.evidence.get("items", {}))},
            "analysis": {
                "issues": list(self.analysis.get("issues", [])),
                "legal_sources": list(self.analysis.get("legal_sources", [])),
                "calculations": list(self.analysis.get("calculations", [])),
                "preliminary_conclusions": list(
                    self.analysis.get("preliminary_conclusions", [])
                ),
                "missing_information": list(self.analysis.get("missing_information", [])),
            },
            "outputs": {"artifacts": list(self.outputs.get("artifacts", []))},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseState":
        state = cls.new()
        if not isinstance(data, dict):
            return state
        state.interaction.update(_dict(data.get("interaction")))
        facts = _dict(data.get("facts"))
        state.facts["items"] = _dict(facts.get("items"))
        state.facts["disputed_facts"] = _list(facts.get("disputed_facts"))
        evidence = _dict(data.get("evidence"))
        state.evidence["items"] = _dict(evidence.get("items"))
        analysis = _dict(data.get("analysis"))
        for key in (
            "issues",
            "legal_sources",
            "calculations",
            "preliminary_conclusions",
            "missing_information",
        ):
            state.analysis[key] = _list(analysis.get(key))
        outputs = _dict(data.get("outputs"))
        state.outputs["artifacts"] = _list(outputs.get("artifacts"))
        return state


@dataclass
class CaseWorkspace:
    workspace_id: str
    session_id: str
    role_id: str
    case_state: CaseState
    messages: list[Any] = field(default_factory=list)
    handoffs: list[Any] = field(default_factory=list)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0
    last_patch_results: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, session_id: str, role_id: str) -> "CaseWorkspace":
        state = CaseState.new()
        state.interaction["user_role"] = role_id
        return cls(
            workspace_id=uuid4().hex,
            session_id=session_id,
            role_id=role_id,
            case_state=state,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "role_id": self.role_id,
            "case_state": self.case_state.to_dict(),
            "version": int(self.version),
            "last_patch_results": [dict(item) for item in self.last_patch_results],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseWorkspace":
        workspace = cls(
            workspace_id=str(data.get("workspace_id") or uuid4().hex),
            session_id=str(data.get("session_id", "")),
            role_id=str(data.get("role_id", "")),
            case_state=CaseState.from_dict(_dict(data.get("case_state"))),
            version=int(data.get("version", data.get("case_version", 0))),
            last_patch_results=[
                dict(item) for item in _list(data.get("last_patch_results")) if isinstance(item, dict)
            ],
        )
        workspace.case_state.interaction["user_role"] = workspace.role_id
        return workspace

