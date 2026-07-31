import unittest
from pathlib import Path

from Agents.scene_catalog import (
    ROLE_IDS,
    SCENE_IDS,
    get_role_scene_template_path,
    get_scene_template_filename,
    list_module_scene_hints,
    list_role_modules,
    summarize_catalog,
    validate_module_key,
    validate_role_id,
    validate_scene_id,
    validate_template_catalog,
)


class SceneCatalogTests(unittest.TestCase):
    def test_validate_scene_id(self):
        self.assertEqual(validate_scene_id("recruitment_probation"), "recruitment_probation")
        with self.assertRaises(ValueError):
            validate_scene_id("unknown_scene")

    def test_validate_role_and_module(self):
        self.assertEqual(validate_role_id("worker"), "worker")
        self.assertEqual(validate_module_key("law_search"), "law_search")
        with self.assertRaises(ValueError):
            validate_role_id("guest")
        with self.assertRaises(ValueError):
            validate_module_key("unknown_module")

    def test_template_filename(self):
        self.assertEqual(get_scene_template_filename("work_injury"), "work_injury.md")
        self.assertEqual(
            get_role_scene_template_path("employer", "termination_layoff"),
            "Prompt_Template/ScenarioAgents/termination_layoff.md",
        )
        with self.assertRaises(ValueError):
            get_role_scene_template_path("lawyer", "law_case_research")

    def test_all_routable_scenes_have_checked_in_templates(self):
        validate_template_catalog(Path(__file__).resolve().parents[1])

    def test_role_module_mapping(self):
        worker_modules = list_role_modules("worker")
        self.assertIn("compensation_calculator", worker_modules)
        self.assertIn("evidence_checker", worker_modules)

    def test_module_scene_hints(self):
        hints = list_module_scene_hints("lawyer_compensation")
        self.assertIn("termination_layoff", hints)
        for module_key in (
            "compensation_calculator",
            "evidence_checker",
            "strategy_advisor",
            "compliance_scanner",
            "contract_templates",
            "communication_guide",
            "law_search",
            "evidence_organizer",
            "lawyer_compensation",
        ):
            for scene_id in list_module_scene_hints(module_key):
                self.assertIn(scene_id, SCENE_IDS)

    def test_catalog_summary(self):
        summary = summarize_catalog()
        self.assertEqual(summary.scene_count, len(SCENE_IDS))
        self.assertEqual(summary.role_count, len(ROLE_IDS))
        self.assertGreater(summary.module_count, 0)


if __name__ == "__main__":
    unittest.main()
