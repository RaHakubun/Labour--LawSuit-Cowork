import unittest
from decimal import Decimal

from utils.city_wage_data import (
    dataset_meta,
    list_cities,
    list_provinces,
    resolve_city,
    resolve_city_wage,
    resolve_province,
)


class CityWageDataTests(unittest.TestCase):
    def test_dataset_meta(self):
        meta = dataset_meta()
        self.assertEqual(meta["record_count"], 300)
        self.assertEqual(meta["province_count"], 31)
        self.assertTrue(str(meta["source_file"]).endswith(".xlsx"))
        self.assertTrue(meta["source_sha256"])

    def test_resolve_province_and_city(self):
        self.assertEqual(resolve_province("广东"), "广东省")
        self.assertEqual(resolve_city("广东省", "深圳"), "深圳市")
        self.assertEqual(resolve_city("北京", None), "北京")

    def test_resolve_city_wage(self):
        record = resolve_city_wage("广东省", "深圳市")
        self.assertEqual(record.province, "广东省")
        self.assertEqual(record.city, "深圳市")
        self.assertEqual(record.avg_wage, Decimal("14755"))

    def test_list_provinces_and_cities(self):
        provinces = list_provinces()
        self.assertIn("广东省", provinces)
        cities = list_cities("广东")
        self.assertIn("深圳市", cities)

    def test_resolve_city_unknown(self):
        with self.assertRaises(ValueError):
            resolve_city_wage("广东省", "不存在市")


if __name__ == "__main__":
    unittest.main()

