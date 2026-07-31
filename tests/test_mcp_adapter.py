import unittest

from Agents.application.scenario_models import AuthorityRetrievalRequest
from Agents.infrastructure.mcp_adapter import PkulawAuthoritySearchAdapter
from Agents.services.tool_hub import ToolExecutionError


class PkulawAuthoritySearchAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_provider_protocol_into_traceable_authorities(self):
        def provider(service_name, query, token):
            self.assertEqual(service_name, "检索法律法规-语义")
            self.assertEqual(
                query,
                "用人单位口头解除劳动合同的法定程序",
            )
            self.assertEqual(token, "contract-test-token")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '[{"title":"中华人民共和国劳动合同法第四十条",'
                            '"summary":"解除劳动合同需满足法定条件并履行相应程序",'
                            '"url":"https://flk.npc.gov.cn/"}]'
                        ),
                    }
                ]
            }

        adapter = PkulawAuthoritySearchAdapter(
            token="contract-test-token",
            query_callable=provider,
        )
        result = await adapter.search(
            AuthorityRetrievalRequest(
                tool_name="检索法律法规-语义",
                query="  用人单位口头解除劳动合同的法定程序  ",
                purpose="核对解除程序",
            )
        )

        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(
            result.documents[0].title,
            "中华人民共和国劳动合同法第四十条",
        )
        self.assertEqual(len(result.documents[0].content_hash), 64)

    async def test_authentication_error_is_not_retried(self):
        attempts = 0

        def provider(service_name, query, token):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("401 authentication failed")

        adapter = PkulawAuthoritySearchAdapter(
            token="expired-contract-test-token",
            query_callable=provider,
        )
        with self.assertRaises(ToolExecutionError) as caught:
            await adapter.search(
                AuthorityRetrievalRequest(
                    tool_name="检索司法案例-语义",
                    query="绩效不合格且无书面通知的解除争议",
                    purpose="检索相近裁判规则",
                )
            )

        self.assertFalse(caught.exception.retryable)
        self.assertEqual(attempts, 1)


if __name__ == "__main__":
    unittest.main()
