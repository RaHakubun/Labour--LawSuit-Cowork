from __future__ import annotations

from Agents.domain.events import EventEnvelope


class CaseRuntimeError(RuntimeError):
    code = "case_runtime_error"
    retryable = False


class InterruptedOperationError(CaseRuntimeError):
    code = "interrupted_operation"

    def __init__(self) -> None:
        super().__init__(
            "operation was interrupted after it started; it was not re-executed "
            "to avoid duplicating external side effects"
        )


class CaseBusyError(CaseRuntimeError):
    code = "case_busy"
    retryable = True

    def __init__(self) -> None:
        super().__init__("case_busy")


class PatchRejectedError(ValueError):
    code = "patch_rejected"
    retryable = False

    def __init__(self, errors: list[str], events: list[EventEnvelope]) -> None:
        self.errors = errors
        self.events = events
        super().__init__("; ".join(errors))


def error_payload(error: Exception) -> dict[str, object]:
    return {
        "code": str(getattr(error, "code", type(error).__name__)),
        "message": str(error),
        "retryable": bool(getattr(error, "retryable", False)),
    }
