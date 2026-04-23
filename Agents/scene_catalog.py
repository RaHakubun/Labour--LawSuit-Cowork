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
    "rules_policy_effectiveness",
    "flexible_employment_relationship",
    "flexible_platform_employment",
    "law_case_research",
    "legal_qa_proxy",
    "evidence_doc_generator",
)

ROLE_IDS: tuple[str, ...] = ("worker", "employer", "lawyer")

SCENE_TEMPLATE_FILENAMES: dict[str, str] = {
    scene_id: f"{scene_id}.md" for scene_id in SCENE_IDS
}

# role_id + scene_id -> project-relative scenario template path
ROLE_SCENE_TEMPLATE_PATHS: dict[str, dict[str, str]] = {
    "employer": {
        "recruitment_probation": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/recruitment_probation.md",
        "adjustment_transfer": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/adjustment_transfer.md",
        "performance_discipline": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/performance_discipline.md",
        "salary_overtime_social": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/salary_overtime_social.md",
        "leave_medical_period": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/leave_medical_period.md",
        "female_protection": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/补充场景/female_protection.md",
        "work_injury": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/补充场景/work_injury.md",
        "termination_layoff": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/termination_layoff.md",
        "noncompete_confidentiality": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/补充场景/noncompete_confidentiality.md",
        "dispute_arbitration": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/补充场景/dispute_arbitration.md",
        "rules_policy_effectiveness": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/rules_policy_effectiveness.md",
        "flexible_employment_relationship": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/flexible_employment_relationship.md",
        "flexible_platform_employment": "scenario agents修改/亚宸-Labourer_ScenarioAgents/flexible_platform_employment.md",
        "law_case_research": "scenario agents修改/檬檬-律师侧 scenario agents/law_case_research.md",
        "legal_qa_proxy": "scenario agents修改/檬檬-律师侧 scenario agents/legal_qa_proxy.md",
        "evidence_doc_generator": "scenario agents修改/檬檬-律师侧 scenario agents/evidence_doc_generator.md",
    },
    "worker": {
        "recruitment_probation": "scenario agents修改/亚宸-Labourer_ScenarioAgents/recruitment_probation.md",
        "adjustment_transfer": "scenario agents修改/亚宸-Labourer_ScenarioAgents/adjustment_transfer.md",
        "performance_discipline": "Prompt_Template/ScenarioAgents/performance_discipline.md",
        "salary_overtime_social": "scenario agents修改/亚宸-Labourer_ScenarioAgents/salary_overtime_social.md",
        "leave_medical_period": "scenario agents修改/亚宸-Labourer_ScenarioAgents/leave_medical_period.md",
        "female_protection": "scenario agents修改/亚宸-Labourer_ScenarioAgents/female_protection.md",
        "work_injury": "scenario agents修改/亚宸-Labourer_ScenarioAgents/work_injury.md",
        "termination_layoff": "scenario agents修改/亚宸-Labourer_ScenarioAgents/termination_layoff.md",
        "noncompete_confidentiality": "scenario agents修改/亚宸-Labourer_ScenarioAgents/noncompete_confidentiality.md",
        "dispute_arbitration": "Prompt_Template/ScenarioAgents/dispute_arbitration.md",
        "rules_policy_effectiveness": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/rules_policy_effectiveness.md",
        "flexible_employment_relationship": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/flexible_employment_relationship.md",
        "flexible_platform_employment": "scenario agents修改/亚宸-Labourer_ScenarioAgents/flexible_platform_employment.md",
        "law_case_research": "scenario agents修改/檬檬-律师侧 scenario agents/law_case_research.md",
        "legal_qa_proxy": "scenario agents修改/檬檬-律师侧 scenario agents/legal_qa_proxy.md",
        "evidence_doc_generator": "scenario agents修改/檬檬-律师侧 scenario agents/evidence_doc_generator.md",
    },
    "lawyer": {
        "recruitment_probation": "Prompt_Template/ScenarioAgents/recruitment_probation.md",
        "adjustment_transfer": "Prompt_Template/ScenarioAgents/adjustment_transfer.md",
        "performance_discipline": "Prompt_Template/ScenarioAgents/performance_discipline.md",
        "salary_overtime_social": "Prompt_Template/ScenarioAgents/salary_overtime_social.md",
        "leave_medical_period": "Prompt_Template/ScenarioAgents/leave_medical_period.md",
        "female_protection": "Prompt_Template/ScenarioAgents/female_protection.md",
        "work_injury": "Prompt_Template/ScenarioAgents/work_injury.md",
        "termination_layoff": "Prompt_Template/ScenarioAgents/termination_layoff.md",
        "noncompete_confidentiality": "Prompt_Template/ScenarioAgents/noncompete_confidentiality.md",
        "dispute_arbitration": "Prompt_Template/ScenarioAgents/dispute_arbitration.md",
        "rules_policy_effectiveness": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/rules_policy_effectiveness.md",
        "flexible_employment_relationship": "scenario agents修改/新洪-用人单位侧-ScenarioAgents/高频核心场景/flexible_employment_relationship.md",
        "flexible_platform_employment": "scenario agents修改/亚宸-Labourer_ScenarioAgents/flexible_platform_employment.md",
        "law_case_research": "scenario agents修改/檬檬-律师侧 scenario agents/law_case_research.md",
        "legal_qa_proxy": "scenario agents修改/檬檬-律师侧 scenario agents/legal_qa_proxy.md",
        "evidence_doc_generator": "scenario agents修改/檬檬-律师侧 scenario agents/evidence_doc_generator.md",
    },
}

# Frontend module key -> primary scene(s) mapping.
# Used for cross-checking frontend capability and backend scene routing.
MODULE_SCENE_HINTS: dict[str, tuple[str, ...]] = {
    "compensation_calculator": ("termination_layoff", "salary_overtime_social"),
    "evidence_checker": ("dispute_arbitration", "termination_layoff"),
    "strategy_advisor": ("termination_layoff", "performance_discipline"),
    "compliance_scanner": ("performance_discipline", "adjustment_transfer", "rules_policy_effectiveness"),
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
        "compensation_calculator",
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


def get_role_scene_template_path(role_id: str, scene_id: str) -> str:
    role = validate_role_id(role_id)
    scene = validate_scene_id(scene_id)
    scene_paths = ROLE_SCENE_TEMPLATE_PATHS.get(role, {})
    template_path = scene_paths.get(scene)
    if not template_path:
        raise ValueError(f"missing template path mapping for role_id={role}, scene_id={scene}")
    return template_path


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
