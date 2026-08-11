#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.scene_catalog import ROLE_SCENE_TEMPLATE_PATHS, SCENE_IDS  # noqa: E402


SCENE_ENUM_RE = re.compile(
    r'"scene_id"\s*:\s*\{.*?"enum"\s*:\s*\[(?P<enum>.*?)\]',
    re.S,
)
SCENE_LITERAL_RE = re.compile(r'"scene_id"\s*:\s*"(?P<scene>[^"]+)"')
STRING_RE = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class TemplateAuditResult:
    path: Path
    scene_hint: str
    expected_values: tuple[str, ...]
    enum_values: tuple[str, ...]
    is_match: bool


def _extract_scene_enum_values(text: str) -> tuple[str, ...]:
    match = SCENE_ENUM_RE.search(text)
    if match:
        raw = match.group("enum")
        values = tuple(v.strip() for v in STRING_RE.findall(raw) if v.strip())
        if not values:
            raise ValueError("scene_id enum values empty")
        return values

    # Some templates use fixed literal scene_id in sample JSON rather than enum schema.
    literals: list[str] = []
    for item in SCENE_LITERAL_RE.finditer(text):
        scene = item.group("scene").strip()
        if scene and scene not in literals:
            literals.append(scene)
    if literals:
        return tuple(literals)
    raise ValueError("scene_id enum/literal definition not found")


def _audit_template(path: Path, expected_values: tuple[str, ...]) -> TemplateAuditResult:
    text = path.read_text(encoding="utf-8")
    enum_values = _extract_scene_enum_values(text)
    scene_hint = path.stem
    is_match = expected_values == enum_values
    return TemplateAuditResult(
        path=path,
        scene_hint=scene_hint,
        expected_values=expected_values,
        enum_values=enum_values,
        is_match=is_match,
    )


def _self_containment_errors(
    path: Path,
    *,
    required_markers: tuple[str, ...],
    minimum_lines: int,
) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors = [
        f"缺少 `{marker}`"
        for marker in required_markers
        if marker not in text
    ]
    line_count = len(text.splitlines())
    if line_count < minimum_lines:
        errors.append(f"仅 {line_count} 行，低于自包含门槛 {minimum_lines} 行")
    if text.count("## 运行时输入（渲染为独立 user message）") != 1:
        errors.append("运行时输入标记必须恰好出现一次")
    return errors


def _to_markdown(
    results: list[TemplateAuditResult],
    self_containment: dict[Path, list[str]],
) -> str:
    lines: list[str] = []
    lines.append("# Prompt Template 审计报告")
    lines.append("")
    lines.append("## 审计目标")
    lines.append("- Controller 模板的场景目录必须与后端全部可路由场景精确一致。")
    lines.append("- 每个 Scenario 模板的固定 `scene_id` 必须与对应场景精确一致。")
    lines.append("- Controller、Legal 和每个 Scenario 模板必须独立包含角色、输入占位和输出契约。")
    lines.append("- 不做自动修复，只暴露问题。")
    lines.append("")
    lines.append("## 后端场景目录")
    lines.append(", ".join(SCENE_IDS))
    lines.append("")
    lines.append("## 模板明细")
    lines.append("")
    lines.append("| 模板 | 场景提示 | 期望 enum | 实际 enum 值数量 | 实际 enum 值 | 匹配 |")
    lines.append("|---|---|---|---:|---|---|")
    for item in results:
        expected_text = ", ".join(item.expected_values)
        enum_text = ", ".join(item.enum_values)
        lines.append(
            f"| `{item.path.name}` | `{item.scene_hint}` | {expected_text} | {len(item.enum_values)} | "
            f"{enum_text} | {'是' if item.is_match else '否'} |"
        )

    mismatches = [r for r in results if not r.is_match]
    lines.append("")
    lines.append("## 自包含审计")
    lines.append("")
    lines.append("| 模板 | 自包含 | 问题 |")
    lines.append("|---|---|---|")
    for path, errors in self_containment.items():
        lines.append(
            f"| `{path.name}` | {'否' if errors else '是'} | "
            f"{'；'.join(errors) if errors else '-'} |"
        )

    containment_errors = [
        error
        for errors in self_containment.values()
        for error in errors
    ]
    lines.append("")
    lines.append("## 结论")
    if mismatches or containment_errors:
        lines.append(f"- 发现 {len(mismatches)} 个模板的 `scene_id.enum` 与期望值不一致。")
        lines.append(f"- 发现 {len(containment_errors)} 个提示词自包含问题。")
        lines.append("- 这些问题将直接影响多场景路由一致性。")
        lines.append("- 必须尽快修复模板协议，否则上线链路不可信。")
    else:
        lines.append("- Controller 场景目录精确匹配，全部 Agent 模板均通过自包含与输入占位审计。")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Prompt_Template scene_id enum consistency")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--output",
        default="PROMPT_TEMPLATE_AUDIT.md",
        help="Output markdown path relative to root",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    controller_template = root / "Prompt_Template" / "ControllerAgent.md"
    if not controller_template.exists():
        raise FileNotFoundError(f"controller template not found: {controller_template}")

    controller_result = _audit_template(controller_template, tuple(SCENE_IDS))
    results: list[TemplateAuditResult] = [controller_result]
    self_containment: dict[Path, list[str]] = {
        controller_template: _self_containment_errors(
            controller_template,
            required_markers=(
                "唯一直接与用户交谈",
                "{output_schema}",
                "{user_input}",
                "{conversation_context}",
                "{attachments_meta}",
            ),
            minimum_lines=120,
        )
    }
    audited_scene_paths: set[tuple[str, Path]] = set()
    for role_id, scene_map in ROLE_SCENE_TEMPLATE_PATHS.items():
        for scene_id, rel_path in sorted(scene_map.items()):
            path = (root / rel_path).resolve()
            if not path.exists():
                raise FileNotFoundError(
                    f"scenario template not found for role_id={role_id}, scene_id={scene_id}: {path}"
                )
            scene_path = (scene_id, path)
            if scene_path in audited_scene_paths:
                continue
            audited_scene_paths.add(scene_path)
            item = _audit_template(path, (scene_id,))
            self_containment[path] = _self_containment_errors(
                path,
                required_markers=(
                    "不直接与用户对话",
                    "你能看到的全部内容只有",
                    "## 关键事实",
                    "## 证据重点",
                    "## 检索与计算",
                    "## 工具语义与计算语义",
                    "candidate_facts",
                    "missing_fact_questions",
                    "evidence_requirements",
                    "retrieval_plan",
                    "rule_calculation_requests",
                    "{output_schema}",
                    "{user_role}",
                    "{user_input}",
                    "{conversation_context}",
                    "{attachments_meta}",
                ),
                minimum_lines=85,
            )
            results.append(
                TemplateAuditResult(
                    path=item.path,
                    scene_hint=scene_id,
                    expected_values=item.expected_values,
                    enum_values=item.enum_values,
                    is_match=item.is_match,
                )
            )

    legal_template = root / "Prompt_Template" / "LegalAnalysisAgent.md"
    if not legal_template.exists():
        raise FileNotFoundError(f"legal template not found: {legal_template}")
    self_containment[legal_template] = _self_containment_errors(
        legal_template,
        required_markers=(
            "受控投影",
            "{output_schema}",
            "{output_task}",
            "{document_type}",
            "{Scenario_Agent_Input}",
            "{Tool_Call_History}",
        ),
        minimum_lines=140,
    )

    report = _to_markdown(results, self_containment)
    output_path = (root / args.output).resolve()
    output_path.write_text(report, encoding="utf-8")

    mismatches = [r for r in results if not r.is_match]
    containment_errors = [
        error
        for errors in self_containment.values()
        for error in errors
    ]
    if mismatches or containment_errors:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
