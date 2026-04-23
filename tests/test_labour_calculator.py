import unittest

from utils.labour_calculator import LabourCalculatorEngine


class LabourCalculatorEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = LabourCalculatorEngine()

    def test_calculate_wage_base(self):
        result = self.engine.calculate(
            "wage_base",
            {"monthly_wage": 21750},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["daily_wage"], 1000.0)
        self.assertEqual(result["result"]["hourly_wage"], 125.0)

    def test_calculate_overtime(self):
        result = self.engine.calculate(
            "overtime",
            {
                "monthly_base_salary": 21750,
                "weekday_overtime_hours": 8,
                "restday_overtime_hours": 8,
                "holiday_overtime_hours": 8,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["weekday_overtime_pay"], 1500.0)
        self.assertEqual(result["breakdown"]["restday_overtime_pay"], 2000.0)
        self.assertEqual(result["breakdown"]["holiday_overtime_pay"], 3000.0)
        self.assertEqual(result["result"]["total_overtime_pay"], 6500.0)

    def test_calculate_severance_with_caps_and_illegal_mode(self):
        result = self.engine.calculate(
            "severance",
            {
                "monthly_avg_wage_12m": 50000,
                "province": "广东省",
                "city": "深圳市",
                "start_date": "2010-01-01",
                "end_date": "2026-01-01",
                "mode": "illegal_termination",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["raw_n"], 16.0)
        self.assertEqual(result["breakdown"]["capped_n"], 12.0)
        self.assertEqual(result["breakdown"]["wage_base"], 44265.0)
        self.assertEqual(result["breakdown"]["province"], "广东省")
        self.assertEqual(result["breakdown"]["city"], "深圳市")
        self.assertEqual(result["result"]["severance_amount"], 1062360.0)

    def test_calculate_severance_n_plus_1_from_service_years(self):
        result = self.engine.calculate(
            "severance",
            {
                "monthly_avg_wage_12m": 10000,
                "province": "北京",
                "city": "北京",
                "service_years": 3.2,
                "mode": "n_plus_1",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["raw_n"], 3.5)
        self.assertEqual(result["breakdown"]["payable_months"], 4.5)
        self.assertEqual(result["result"]["severance_amount"], 45000.0)

    def test_calculate_severance_requires_province_city(self):
        result = self.engine.calculate(
            "severance",
            {
                "monthly_avg_wage_12m": 10000,
                "service_years": 1,
                "mode": "normal",
            },
        )
        self.assertFalse(result["ok"])
        self.assertIn("province is required", result["errors"][0])

    def test_calculate_medical_period_national(self):
        result = self.engine.calculate(
            "medical_period",
            {
                "is_shanghai": False,
                "total_work_years": 12,
                "current_company_years": 7,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["medical_period_months"], 9)
        self.assertEqual(result["result"]["accumulation_cycle_months"], 15)

    def test_calculate_medical_period_shanghai(self):
        result = self.engine.calculate(
            "medical_period",
            {
                "is_shanghai": True,
                "total_work_years": 12,
                "current_company_years": 3.8,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["medical_period_months"], 5)

    def test_calculate_medical_period_with_dates(self):
        result = self.engine.calculate(
            "medical_period",
            {
                "is_shanghai": False,
                "first_work_date": "2016-01-01",
                "current_company_start_date": "2019-01-01",
                "reference_date": "2026-01-01",
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["medical_period_months"], 9)
        self.assertEqual(result["result"]["accumulation_cycle_months"], 15)

    def test_calculate_annual_leave_unused_full_year(self):
        result = self.engine.calculate(
            "annual_leave_unused",
            {
                "monthly_wage": 21750,
                "total_work_years": 12,
                "taken_leave_days": 3,
                "is_current_year_join_or_leave": False,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["annual_leave_days"], 10)
        self.assertEqual(result["breakdown"]["unused_leave_days"], 7.0)
        self.assertEqual(result["result"]["unused_annual_leave_extra_pay"], 14000.0)

    def test_calculate_annual_leave_unused_prorated(self):
        result = self.engine.calculate(
            "annual_leave_unused",
            {
                "monthly_wage": 21750,
                "total_work_years": 12,
                "taken_leave_days": 3,
                "is_current_year_join_or_leave": True,
                "current_year_days_in_company": 200,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["prorated_annual_leave_days"], 5)
        self.assertEqual(result["breakdown"]["unused_leave_days"], 2.0)
        self.assertEqual(result["result"]["unused_annual_leave_extra_pay"], 4000.0)

    def test_calculate_annual_leave_unused_clamps_negative_unused(self):
        result = self.engine.calculate(
            "annual_leave_unused",
            {
                "monthly_wage": 21750,
                "total_work_years": 12,
                "taken_leave_days": 8,
                "is_current_year_join_or_leave": True,
                "current_year_days_in_company": 200,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["unused_leave_days"], 0.0)
        self.assertEqual(result["result"]["unused_annual_leave_extra_pay"], 0.0)

    def test_calculate_double_wage_sub_one_month(self):
        result = self.engine.calculate(
            "double_wage_unsigned_contract",
            {
                "entry_date": "2026-01-01",
                "unsigned_end_date": "2026-02-10",
                "monthly_wage": 10000,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["unsigned_start_date"], "2026-02-01")
        self.assertEqual(result["breakdown"]["calc_end_date"], "2026-02-10")
        self.assertEqual(len(result["breakdown"]["segments"]), 1)
        self.assertEqual(result["breakdown"]["segments"][0]["weekdays"], 7)
        self.assertAlmostEqual(result["result"]["double_wage_gap"], 3218.39, places=2)

    def test_calculate_double_wage_under_12_months(self):
        result = self.engine.calculate(
            "double_wage_unsigned_contract",
            {
                "entry_date": "2026-01-01",
                "unsigned_end_date": "2026-05-01",
                "monthly_wage": 10000,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["breakdown"]["segments"]), 4)
        self.assertTrue(result["breakdown"]["segments"][0]["is_full_month"])
        self.assertEqual(result["breakdown"]["segments"][-1]["weekdays"], 1)
        self.assertAlmostEqual(result["result"]["double_wage_gap"], 30459.77, places=2)

    def test_calculate_double_wage_cap_11_months(self):
        result = self.engine.calculate(
            "double_wage_unsigned_contract",
            {
                "entry_date": "2025-01-01",
                "unsigned_end_date": "2026-03-01",
                "monthly_wage": 10000,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["breakdown"]["calc_end_date"], "2025-12-31")
        self.assertEqual(len(result["breakdown"]["segments"]), 11)
        self.assertEqual(result["result"]["double_wage_gap"], 110000.0)

    def test_unknown_calc_type(self):
        result = self.engine.calculate("unknown_type", {})
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_invalid_input_returns_error(self):
        result = self.engine.calculate(
            "overtime",
            {"monthly_base_salary": -1},
        )
        self.assertFalse(result["ok"])
        self.assertIn("must be >= 0", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
