from __future__ import annotations

from dataclasses import dataclass


SCENE_IDS: tuple[str, ...] = (
    "recruitment_probation",
    "adjustment_transfer",
    "performance_discipline",
    "salary_overtime_social",
    "leave_medical_period",
    "female_protection",
    "work_injury",
    "termination_layoff",
    "noncompete_confidentiality",
    "dispute_arbitration",
)

ROLE_IDS: tuple[str, ...] = ("worker", "employer", "lawyer")

SCENE_TEMPLATE_FILENAMES: dict[str, str] = {
    scene_id: f"{scene_id}.md" for scene_id in SCENE_IDS
}

# Frontend module key -> primary scene(s) mapping.
# Used for cross-checking frontend capability and backend scene routing.
MODULE_SCENE_HINTS: dict[str, tuple[str, ...]] = {
    "compensation_calculator": ("termination_layoff", "salary_overtime_social"),
    "evidence_checker": ("dispute_arbitration", "termination_layoff"),
    "strategy_advisor": ("termination_layoff", "performance_discipline"),
    "compliance_scanner": ("performance_discipline", "adjustment_transfer"),
    "contract_templates": ("recruitment_probation",),
    "communication_guide": ("performance_discipline", "termination_layoff"),
    "law_search": ("dispute_arbitration",),
    "evidence_organizer": ("dispute_arbitration", "work_injury"),
    "lawyer_compensation": ("termination_layoff", "salary_overtime_social"),
}

ROLE_MODULE_KEYS: dict[str, tuple[str, ...]] = {
    "worker": (
        "compensation_calculator",
        "evidence_checker",
        "strategy_advisor",
    ),
    "employer": (
        "compliance_scanner",
        "contract_templates",
        "communication_guide",
    ),
    "lawyer": (
        "law_search",
        "evidence_organizer",
        "lawyer_compensation",
    ),
}


@dataclass(frozen=True)
class SceneCatalogSummary:
    scene_count: int
    role_count: int
    module_count: int


def validate_scene_id(scene_id: str) -> str:
    normalized = str(scene_id).strip()
    if normalized not in SCENE_TEMPLATE_FILENAMES:
        allowed = ", ".join(SCENE_IDS)
        raise ValueError(f"invalid scene_id: {scene_id}. allowed: {allowed}")
    return normalized


def validate_role_id(role_id: str) -> str:
    normalized = str(role_id).strip()
    if normalized not in ROLE_MODULE_KEYS:
        allowed = ", ".join(ROLE_IDS)
        raise ValueError(f"invalid role_id: {role_id}. allowed: {allowed}")
    return normalized


def validate_module_key(module_key: str) -> str:
    normalized = str(module_key).strip()
    if normalized not in MODULE_SCENE_HINTS:
        allowed = ", ".join(sorted(MODULE_SCENE_HINTS.keys()))
        raise ValueError(f"invalid module_key: {module_key}. allowed: {allowed}")
    return normalized


def get_scene_template_filename(scene_id: str) -> str:
    scene = validate_scene_id(scene_id)
    return SCENE_TEMPLATE_FILENAMES[scene]


def list_scene_ids() -> list[str]:
    return list(SCENE_IDS)


def list_role_ids() -> list[str]:
    return list(ROLE_IDS)


def list_role_modules(role_id: str) -> list[str]:
    role = validate_role_id(role_id)
    return list(ROLE_MODULE_KEYS[role])


def list_module_scene_hints(module_key: str) -> list[str]:
    module = validate_module_key(module_key)
    return list(MODULE_SCENE_HINTS[module])


def summarize_catalog() -> SceneCatalogSummary:
    return SceneCatalogSummary(
        scene_count=len(SCENE_IDS),
        role_count=len(ROLE_IDS),
        module_count=len(MODULE_SCENE_HINTS),
    )

