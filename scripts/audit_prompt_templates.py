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

from Agents.scene_catalog import SCENE_IDS


SCENE_ENUM_RE = re.compile(
    r'"scene_id"\s*:\s*\{.*?"enum"\s*:\s*\[(?P<enum>.*?)\]',
    re.S,
)
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
    if not match:
        raise ValueError("scene_id enum definition not found")
    raw = match.group("enum")
    values = tuple(v.strip() for v in STRING_RE.findall(raw) if v.strip())
    if not values:
        raise ValueError("scene_id enum values empty")
    return values


def _load_templates(root: Path) -> list[Path]:
    scenario_dir = root / "Prompt_Template" / "ScenarioAgents"
    if not scenario_dir.exists():
        raise FileNotFoundError(f"scenario template dir not found: {scenario_dir}")
    return sorted(scenario_dir.glob("*.md"))


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


def _to_markdown(results: list[TemplateAuditResult]) -> str:
    lines: list[str] = []
    lines.append("# Prompt Template 审计报告")
    lines.append("")
    lines.append("## 审计目标")
    lines.append("- 检查每个场景模板中的 `analysis.scene_id.enum` 是否与后端场景目录一致。")
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
    lines.append("## 结论")
    if mismatches:
        lines.append(f"- 发现 {len(mismatches)} 个模板的 `scene_id.enum` 与期望值不一致。")
        lines.append("- 这些问题将直接影响多场景路由一致性。")
        lines.append("- 必须尽快修复模板协议，否则上线链路不可信。")
    else:
        lines.append("- 全部模板的 `scene_id.enum` 与后端目录一致。")
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
    templates = _load_templates(root)
    controller_template = root / "Prompt_Template" / "ControllerAgent.md"
    if not controller_template.exists():
        raise FileNotFoundError(f"controller template not found: {controller_template}")

    results: list[TemplateAuditResult] = [
        _audit_template(controller_template, tuple(SCENE_IDS))
    ]
    for path in templates:
        results.append(_audit_template(path, (path.stem,)))

    report = _to_markdown(results)
    output_path = (root / args.output).resolve()
    output_path.write_text(report, encoding="utf-8")

    mismatches = [r for r in results if not r.is_match]
    if mismatches:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
