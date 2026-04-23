from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Optional

from .agent import Agent, DEFAULT_STOP_WORDS
from .legal_analysis_agent import LEGAL_ANALYSIS_TEMPLATE_DEFAULT, LegalAnalysisAgent
from .scenario_agent import (
    DEFAULT_MCP_RETRIES,
    SCENARIO_TEMPLATE_ROOT,
    ScenarioAgent,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_TEMPLATE_DEFAULT = "Prompt_Template/ControllerAgent.md"


@dataclass
class PipelineRunResult:
    controller_output: dict[str, Any]
    scenario_output: dict[str, Any]
    scenario_tool_call_history: str
    legal_result: dict[str, Any]
    transcript: list[tuple[str, str]]


@dataclass
class ControllerScenarioLegalPipeline:
    controller_agent: Agent = field(default_factory=lambda: Agent(main_prompt=""))
    scenario_agent: ScenarioAgent = field(default_factory=ScenarioAgent)
    legal_analysis_agent: LegalAnalysisAgent = field(default_factory=LegalAnalysisAgent)

    controller_template_path: str = CONTROLLER_TEMPLATE_DEFAULT
    legal_template_path: str = LEGAL_ANALYSIS_TEMPLATE_DEFAULT

    max_controller_rounds: int = 12
    max_scenario_rounds: int = 12
    max_scene_repair_rounds: int = 2
    scenario_tool_retry_times: int = DEFAULT_MCP_RETRIES
    scenario_tool_continue_on_error: bool = True

    transcript: list[tuple[str, str]] = field(default_factory=list, init=False)

    def _append_transcript(self, role: str, content: str) -> None:
        self.transcript.append((role, content))

    def _extract_json_payload(self, text: str, agent_name: str) -> dict[str, Any]:
        raw = text.strip()
        candidates: list[str] = []

        if raw.startswith("```"):
            fence_parts = raw.split("```")
            for i in range(1, len(fence_parts), 2):
                block = fence_parts[i].strip()
                if not block:
                    continue
                if "\n" in block and block.split("\n", 1)[0].strip().lower() == "json":
                    block = block.split("\n", 1)[1].strip()
                candidates.append(block)

        candidates.append(raw)
        if "{" in raw and "}" in raw:
            candidates.append(raw[raw.find("{") : raw.rfind("}") + 1])

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload
        raise ValueError(f"{agent_name} output is not valid JSON object.")

    def _extract_askmore(self, payload: dict[str, Any], agent_name: str) -> str:
        askmore = payload.get("askmore")
        if isinstance(askmore, str):
            normalized = askmore.strip().lower()
            if normalized in {"yes", "no"}:
                return normalized
        raise ValueError(f'Missing or invalid "askmore" in {agent_name} output.')

    def _resolve_scenario_template(self, scene_id: str) -> str:
        scene = scene_id.strip()
        if not scene:
            raise ValueError("scene_id is empty.")
        path = (SCENARIO_TEMPLATE_ROOT / f"{scene}.md").resolve()
        if not path.exists():
            raise FileNotFoundError(f"Scenario template not found for scene_id={scene}: {path}")
        return str(path)

    def _extract_scene_id(self, payload: dict[str, Any]) -> str:
        analysis = payload.get("analysis")
        if isinstance(analysis, dict):
            scene_id = analysis.get("scene_id")
            if isinstance(scene_id, str):
                return scene_id.strip()
        scene_id = payload.get("scene_id")
        if isinstance(scene_id, str):
            return scene_id.strip()
        return ""

    def _run_controller_stage(
        self,
        initial_user_input: str,
        attachments_meta: Optional[Any],
        input_func: Callable[[str], str],
        output_func: Callable[[str], None],
        stop_words: set[str],
        controller_kwargs: dict[str, Any],
    ) -> Optional[tuple[dict[str, Any], str, str]]:
        current_input = initial_user_input
        scene_repair_count = 0

        for round_index in range(1, self.max_controller_rounds + 1):
            output_func(f"[Active Agent] ControllerAgent (Round {round_index})")
            turn = self.controller_agent.run_turn(
                user_input=current_input,
                template_path=self.controller_template_path,
                attachments_meta=attachments_meta,
                **controller_kwargs,
            )
            payload = self._extract_json_payload(turn.agent_reply, "ControllerAgent")
            askmore = self._extract_askmore(payload, "ControllerAgent")

            self._append_transcript("User", turn.user_input)
            output_func(f"User: {turn.user_input}")

            if askmore == "yes":
                ask_text = payload.get("ask")
                if isinstance(ask_text, str) and ask_text.strip():
                    agent_text = ask_text.strip()
                else:
                    agent_text = turn.agent_reply
                self._append_transcript("ControllerAgent", agent_text)
                output_func(f"ControllerAgent: {agent_text}")

                next_input = input_func("User> ").strip()
                if next_input in stop_words:
                    output_func("Session ended.")
                    return None
                current_input = next_input
                continue

            self._append_transcript("ControllerAgent", turn.agent_reply)
            output_func("ControllerAgent: 路由信息已生成。")

            scene_id = self._extract_scene_id(payload)
            if not scene_id:
                scene_repair_count += 1
                if scene_repair_count > self.max_scene_repair_rounds:
                    raise ValueError("Controller output missing scene_id after repair retries.")
                output_func(
                    f"[Controller Protocol Warning] scene_id 缺失，触发修复轮次 {scene_repair_count}。"
                )
                current_input = (
                    "协议修复：你上一轮输出缺少 analysis.scene_id。"
                    "请严格按模板输出合法 JSON，并在 askmore=no 时给出 scene_id。"
                )
                continue

            try:
                scenario_template_path = self._resolve_scenario_template(scene_id)
            except Exception:
                scene_repair_count += 1
                if scene_repair_count > self.max_scene_repair_rounds:
                    raise
                output_func(
                    f"[Controller Protocol Warning] scene_id={scene_id} 无效，触发修复轮次 {scene_repair_count}。"
                )
                current_input = (
                    "协议修复：你上一轮 scene_id 对应模板不存在。"
                    "请严格按模板输出合法 JSON，并提供有效 scene_id。"
                )
                continue

            return payload, scenario_template_path, turn.user_input

        raise RuntimeError(
            f"ControllerAgent exceeded max_controller_rounds={self.max_controller_rounds}."
        )

    def _run_scenario_stage(
        self,
        initial_user_input: str,
        scenario_template_path: str,
        attachments_meta: Optional[Any],
        input_func: Callable[[str], str],
        output_func: Callable[[str], None],
        stop_words: set[str],
        scenario_kwargs: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        current_input = initial_user_input

        for round_index in range(1, self.max_scenario_rounds + 1):
            output_func(f"[Active Agent] ScenarioAgent (Round {round_index})")
            turn = self.scenario_agent.run_turn(
                user_input=current_input,
                template_path=scenario_template_path,
                attachments_meta=attachments_meta,
                **scenario_kwargs,
            )

            payload = turn.parsed_output
            askmore = turn.askmore

            self._append_transcript("User", turn.user_input)
            output_func(f"User: {turn.user_input}")

            if askmore == "yes":
                ask_text = payload.get("ask")
                if isinstance(ask_text, str) and ask_text.strip():
                    agent_text = ask_text.strip()
                else:
                    agent_text = turn.agent_reply
                self._append_transcript("ScenarioAgent", agent_text)
                output_func(f"ScenarioAgent: {agent_text}")

                next_input = input_func("User> ").strip()
                if next_input in stop_words:
                    output_func("Session ended.")
                    return None
                current_input = next_input
                continue

            self._append_transcript("ScenarioAgent", turn.agent_reply)
            output_func("ScenarioAgent: 场景分析完成，准备交接。")
            return payload

        raise RuntimeError(f"ScenarioAgent exceeded max_scenario_rounds={self.max_scenario_rounds}.")

    def run_console(
        self,
        initial_user_input: str,
        *,
        attachments_meta: Optional[Any] = None,
        stop_words: Optional[set[str]] = None,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
        controller_kwargs: Optional[dict[str, Any]] = None,
        scenario_kwargs: Optional[dict[str, Any]] = None,
        legal_kwargs: Optional[dict[str, Any]] = None,
    ) -> Optional[PipelineRunResult]:
        stop_words = stop_words or DEFAULT_STOP_WORDS
        controller_kwargs = controller_kwargs or {}
        scenario_kwargs = scenario_kwargs or {}
        legal_kwargs = legal_kwargs or {}

        if not initial_user_input.strip():
            output_func("Empty input, session ended.")
            return None

        controller_stage_result = self._run_controller_stage(
            initial_user_input=initial_user_input.strip(),
            attachments_meta=attachments_meta,
            input_func=input_func,
            output_func=output_func,
            stop_words=stop_words,
            controller_kwargs=controller_kwargs,
        )
        if controller_stage_result is None:
            return None
        controller_output, scenario_template_path, controller_last_user_input = controller_stage_result

        scenario_initial_input = controller_output.get("user_input")
        if not isinstance(scenario_initial_input, str) or not scenario_initial_input.strip():
            scenario_initial_input = controller_last_user_input

        output_func(
            f"[Handoff] ControllerAgent -> ScenarioAgent (template={Path(scenario_template_path).name})"
        )
        scenario_output = self._run_scenario_stage(
            initial_user_input=scenario_initial_input,
            scenario_template_path=scenario_template_path,
            attachments_meta=controller_output,
            input_func=input_func,
            output_func=output_func,
            stop_words=stop_words,
            scenario_kwargs=scenario_kwargs,
        )
        if scenario_output is None:
            return None

        output_func("[ScenarioAgent] 开始执行 tool 字段对应的 MCP 调用。")
        try:
            scenario_tool_execution = self.scenario_agent.execute_tools_from_payload(
                scenario_output,
                retry_times=self.scenario_tool_retry_times,
                continue_on_error=self.scenario_tool_continue_on_error,
            )
            scenario_tool_history = scenario_tool_execution.tool_call_history
        except Exception as exc:
            if not self.scenario_tool_continue_on_error:
                raise
            scenario_tool_history = (
                "[Scenario Bootstrap] Tool Call(s)\n"
                "result:\n"
                f"Scenario tool execution failed: {exc}"
            )

        if scenario_tool_history:
            output_func("Scenario Tool_Call_History:")
            output_func(scenario_tool_history)
        else:
            output_func("Scenario Tool_Call_History: (empty)")

        output_func("[Handoff] ScenarioAgent -> LegalAnalysisAgent")

        scenario_agent_input = json.dumps(scenario_output, ensure_ascii=False, indent=2)
        legal_result = self.legal_analysis_agent.run_until_done(
            scenario_agent_input=scenario_agent_input,
            template_path=self.legal_template_path,
            initial_tool_call_history=scenario_tool_history,
            **legal_kwargs,
        )

        final_output = legal_result.get("final_output", {})
        self._append_transcript("LegalAnalysisAgent", json.dumps(final_output, ensure_ascii=False))
        output_func("[Active Agent] LegalAnalysisAgent")
        output_func("LegalAnalysisAgent Final JSON:")
        output_func(json.dumps(final_output, ensure_ascii=False, indent=2))

        return PipelineRunResult(
            controller_output=controller_output,
            scenario_output=scenario_output,
            scenario_tool_call_history=scenario_tool_history,
            legal_result=legal_result,
            transcript=list(self.transcript),
        )

