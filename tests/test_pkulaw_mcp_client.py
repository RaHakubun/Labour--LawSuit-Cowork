import unittest

from utils.pkulaw_mcp_client import format_items_for_display, parse_mcp_result


class PkulawMcpClientTests(unittest.TestCase):
    def test_format_items_for_display_accepts_string_items(self):
        items = [
            "周a诉上海A有限公司劳动合同纠纷案",
            "https://pkulaw.com/pfnl/a1.html",
            "西安轻工业钟表研究所有限公司、马莉劳动争议民事一审民事判决书",
            "https://pkulaw.com/pfnl/a2.html",
        ]

        out = format_items_for_display(items)
        self.assertIn("周a诉上海A有限公司劳动合同纠纷案", out)
        self.assertIn("https://pkulaw.com/pfnl/a1.html", out)
        self.assertIn("西安轻工业钟表研究所有限公司、马莉劳动争议民事一审民事判决书", out)

    def test_format_items_for_display_accepts_text_dict(self):
        items = [{"text": "识别到引用法条：民事诉讼法第一百七十条"}]

        out = format_items_for_display(items)
        self.assertIn("识别到引用法条", out)

    def test_parse_mcp_result_parses_json_string_items(self):
        result = {
            "content": [
                {
                    "type": "text",
                    "text": '["案例A","https://pkulaw.com/pfnl/x.html"]',
                }
            ]
        }

        parsed = parse_mcp_result(result)
        self.assertEqual(parsed["items"][0], "案例A")

    def test_parse_mcp_result_keeps_plain_text(self):
        result = {
            "content": [
                {
                    "type": "text",
                    "text": "本院依照《中华人民共和国民事诉讼法》第一百七十条……",
                }
            ]
        }

        parsed = parse_mcp_result(result)
        out = format_items_for_display(parsed["items"])
        self.assertIn("第一百七十条", out)


if __name__ == "__main__":
    unittest.main()
