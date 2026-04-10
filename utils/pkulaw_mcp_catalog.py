from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str | None = None


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    base_url: str
    connection_type: str | None
    tools: list[ToolInfo]
    examples: list[dict[str, Any]]


_SERVICE_URLS: dict[str, str] = {
    "检索法律法规-语义": "https://apim-gateway.pkulaw.com/mcp-law-search-service",
    "修正生成幻觉-法条": "https://apim-gateway.pkulaw.com/pku_citation_validator",
    "法宝超链": "https://apim-gateway.pkulaw.com/add-doc-link",
    "法条识别与溯源": "https://apim-gateway.pkulaw.com/law_recognition",
    "案号识别与溯源": "https://apim-gateway.pkulaw.com/case_number_recognition",
    "检索司法案例-关键词": "https://apim-gateway.pkulaw.com/mcp-case",
    "检索司法案例-语义": "https://apim-gateway.pkulaw.com/mcp-case-search-service",
    "精准查找法条-关键词": "https://apim-gateway.pkulaw.com/mcp-fatiao",
    "检索法律法规-关键词": "https://apim-gateway.pkulaw.com/mcp-law",
    "法宝语义检索（NL-SQL）": "https://apim-gateway.pkulaw.com/assistant/mcp-pkulaw-search",
}


def load_catalog() -> dict[str, Any]:
    services = {
        name: ServiceInfo(
            name=name,
            base_url=base_url,
            connection_type=None,
            tools=[],
            examples=[],
        )
        for name, base_url in _SERVICE_URLS.items()
    }
    return {
        "service_names": list(_SERVICE_URLS.keys()),
        "services": services,
    }
