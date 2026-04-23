import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.scenario_agent import ScenarioAgent


def main() -> None:
    user_input = input("User> ").strip()
    if not user_input:
        print("Empty input, session ended.")
        return

    agent = ScenarioAgent(main_prompt="")
    agent.chat_console(
        initial_user_input=user_input,
        template_path=str(PROJECT_ROOT / "Prompt_Template/ScenarioAgents/recruitment_probation.md"),
    )


if __name__ == "__main__":
    main()
