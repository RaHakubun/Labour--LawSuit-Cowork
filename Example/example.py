import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.agent import Agent


def main() -> None:
    user_input = input("User> ").strip()
    if not user_input:
        print("Empty input, session ended.")
        return

    agent = Agent(main_prompt="")
    agent.chat_console(
        initial_user_input=user_input,
        template_path=str(PROJECT_ROOT / "Prompt_Template/ControllerAgent.md"),
    )


if __name__ == "__main__":
    main()
