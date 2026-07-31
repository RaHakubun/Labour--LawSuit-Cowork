from __future__ import annotations

from .case_state import CaseStage


ALLOWED_TRANSITIONS: dict[CaseStage, set[CaseStage]] = {
    CaseStage.INTAKE: {CaseStage.FACT_COLLECTING},
    CaseStage.FACT_COLLECTING: {
        CaseStage.EVIDENCE_PROCESSING,
        CaseStage.ANALYSIS_READY,
    },
    CaseStage.EVIDENCE_PROCESSING: {
        CaseStage.FACT_COLLECTING,
        CaseStage.ANALYSIS_READY,
    },
    CaseStage.ANALYSIS_READY: {
        CaseStage.FACT_COLLECTING,
        CaseStage.ANALYZING,
    },
    CaseStage.ANALYZING: {
        CaseStage.FACT_COLLECTING,
        CaseStage.ANALYSIS_READY,
        CaseStage.DOCUMENT_READY,
    },
    CaseStage.DOCUMENT_READY: {
        CaseStage.FACT_COLLECTING,
        CaseStage.ANALYSIS_READY,
        CaseStage.COMPLETED,
    },
    CaseStage.COMPLETED: {
        CaseStage.FACT_COLLECTING,
        CaseStage.ANALYSIS_READY,
    },
}


def validate_transition(current: CaseStage, target: CaseStage) -> None:
    if target == current:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"illegal case stage transition: {current.value} -> {target.value}")
