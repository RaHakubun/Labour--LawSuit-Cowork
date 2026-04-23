import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.legal_analysis_agent import LegalAnalysisAgent


def main() -> None:
    scenario_agent_input = input("ScenarioAgentInput> ").strip()
    if not scenario_agent_input:
        print("Empty input, session ended.")
        return

    agent = LegalAnalysisAgent(main_prompt="")
    result = agent.run_until_done(
        scenario_agent_input=scenario_agent_input,
        template_path=str(PROJECT_ROOT / "Prompt_Template/LegalAnalysisAgent.md"),
    )
    print("Final Output:")
    print(result["final_output"])
    print("\nTool Call History:")
    print(result["tool_call_history"])


if __name__ == "__main__":
    main()
