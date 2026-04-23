import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Agents.agent import Agent
from Agents.legal_analysis_agent import LegalAnalysisAgent
from Agents.pipeline_agent import ControllerScenarioLegalPipeline
from Agents.scenario_agent import ScenarioAgent


def main() -> None:
    initial_user_input = input("User> ").strip()
    if not initial_user_input:
        print("Empty input, session ended.")
        return

    pipeline = ControllerScenarioLegalPipeline(
        controller_agent=Agent(main_prompt=""),
        scenario_agent=ScenarioAgent(main_prompt=""),
        legal_analysis_agent=LegalAnalysisAgent(main_prompt=""),
        controller_template_path=str(PROJECT_ROOT / "Prompt_Template/ControllerAgent.md"),
        legal_template_path=str(PROJECT_ROOT / "Prompt_Template/LegalAnalysisAgent.md"),
    )

    result = pipeline.run_console(initial_user_input=initial_user_input)
    if result is None:
        return

    print("\n=== Pipeline Finished ===")
    print("Final legal output:")
    print(result.legal_result["final_output"])


if __name__ == "__main__":
    main()

