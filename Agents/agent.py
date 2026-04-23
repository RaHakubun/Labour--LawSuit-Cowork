from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Optional

from . import llm_call

DEFAULT_STOP_WORDS = {"exit", "quit", "q", "退出"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class TurnResult:
    user_input: str
    conversation_context: str
    injected_prompt: str
    agent_reply: str


@dataclass
class Agent:
    main_prompt: str
    skills: list[str] = field(default_factory=list)
    mcps: list[str] = field(default_factory=list)
    model: Optional[str] = None
    llm_callable: Callable[..., str] = field(default=llm_call.chat_completion, repr=False)
    conversation_messages: list[tuple[str, str]] = field(default_factory=list, init=False)

    def call_skill(self, skill_name: str, *args: Any, **kwargs: Any) -> Any:
        """TODO: 调用 skill 并返回结果。"""
        pass

    def call_mcp(self, mcp_name: str, *args: Any, **kwargs: Any) -> Any:
        """TODO: 调用 mcp 并返回结果。"""
        pass

    def _append_message(self, role: str, content: str) -> None:
        self.conversation_messages.append((role, content))

    def build_conversation_context(self) -> str:
        return "\n".join(f"{role}: {content}" for role, content in self.conversation_messages)

    def _to_attachments_text(self, attachments_meta: Optional[Any]) -> str:
        if attachments_meta is None:
            return "[]"
        if isinstance(attachments_meta, str):
            return attachments_meta
        return json.dumps(attachments_meta, ensure_ascii=False)

    def _inject_template_variables(
        self,
        template: str,
        user_input: str,
        conversation_context: str,
        attachments_meta: Optional[Any] = None,
    ) -> str:
        rendered = template.replace("{user_input}", user_input)
        rendered = rendered.replace("{conversation_context}", conversation_context)
        rendered = rendered.replace("{attachments_meta}", self._to_attachments_text(attachments_meta))
        return rendered

    def build_user_prompt(
        self,
        prompt: str,
        template_path: Optional[str] = None,
        attachments_meta: Optional[Any] = None,
    ) -> str:
        if not template_path:
            return prompt
        template = self._read_template(template_path)
        return self._inject_template_variables(
            template=template,
            user_input=prompt,
            conversation_context=self.build_conversation_context(),
            attachments_meta=attachments_meta,
        )

    def _read_template(self, template_path: str) -> str:
        path = Path(template_path)
        if path.is_absolute():
            resolved = path
        else:
            project_candidate = PROJECT_ROOT / path
            cwd_candidate = Path.cwd() / path
            if project_candidate.exists():
                resolved = project_candidate
            else:
                resolved = cwd_candidate
        return resolved.read_text(encoding="utf-8")

    def run_turn(
        self,
        user_input: str,
        template_path: str = "Prompt_Template/ControllerAgent.md",
        attachments_meta: Optional[Any] = None,
        on_token: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ) -> TurnResult:
        self._append_message("User", user_input)
        conversation_context = self.build_conversation_context()
        injected_prompt = self.build_user_prompt(
            prompt=user_input,
            template_path=template_path,
            attachments_meta=attachments_meta,
        )
        system_prompt = self.main_prompt if self.main_prompt.strip() else None
        agent_reply = llm_call.invoke_llm(
            self.llm_callable,
            user_prompt=injected_prompt,
            system_prompt=system_prompt,
            model=self.model,
            on_token=on_token,
            **kwargs,
        )
        self._append_message("Agent", agent_reply)
        return TurnResult(
            user_input=user_input,
            conversation_context=conversation_context,
            injected_prompt=injected_prompt,
            agent_reply=agent_reply,
        )

    def run(self, prompt: str, template_path: Optional[str] = None, **kwargs: Any) -> str:
        result = self.run_turn(
            user_input=prompt,
            template_path=template_path or "Prompt_Template/ControllerAgent.md",
            **kwargs,
        )
        return result.agent_reply

    def _parse_controller_output(self, agent_reply: str) -> dict[str, Any]:
        text = agent_reply.strip()
        candidates: list[str] = []

        if text.startswith("```"):
            fence_parts = text.split("```")
            for i in range(1, len(fence_parts), 2):
                block = fence_parts[i].strip()
                if not block:
                    continue
                if "\n" in block and block.split("\n", 1)[0].strip().lower() == "json":
                    block = block.split("\n", 1)[1].strip()
                candidates.append(block)

        candidates.append(text)
        if "{" in text and "}" in text:
            candidates.append(text[text.find("{") : text.rfind("}") + 1])

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return payload

        raise ValueError("Controller output is not valid JSON object.")

    def _should_continue_asking(self, agent_reply: str) -> bool:
        payload = self._parse_controller_output(agent_reply)
        askmore = payload.get("askmore")

        if isinstance(askmore, str):
            normalized = askmore.strip().lower()
            if normalized == "yes":
                return True
            if normalized == "no":
                return False
        if isinstance(askmore, bool):
            return askmore

        raise ValueError('Missing or invalid "askmore" in controller output.')

    def chat_console(
        self,
        initial_user_input: str,
        template_path: str = "Prompt_Template/ControllerAgent.md",
        attachments_meta: Optional[Any] = None,
        stop_words: Optional[set[str]] = None,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
        **kwargs: Any,
    ) -> None:
        stop_words = stop_words or DEFAULT_STOP_WORDS
        current_input = initial_user_input
        round_index = 1

        while True:
            try:
                result = self.run_turn(
                    user_input=current_input,
                    template_path=template_path,
                    attachments_meta=attachments_meta,
                    **kwargs,
                )
            except Exception as exc:
                output_func(f"AgentError: {exc}")
                output_func("Session ended.")
                break
            output_func(f"[Round {round_index}]")
            output_func(f"User: {result.user_input}")
            output_func("Conversation Context:")
            output_func(result.conversation_context)
            output_func("Injected Prompt:")
            output_func(result.injected_prompt)
            output_func(f"Agent: {result.agent_reply}")

            try:
                should_continue = self._should_continue_asking(result.agent_reply)
            except Exception as exc:
                output_func(f"AgentProtocolError: {exc}")
                output_func("Session ended.")
                break

            if not should_continue:
                output_func('askmore=no, session ended.')
                break

            next_input = input_func("User> ").strip()
            if next_input in stop_words:
                output_func("Session ended.")
                break
            current_input = next_input
            round_index += 1
