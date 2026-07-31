import types
from typing import Any

import json
import os
try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover - exercised via runtime checks
    requests = None  # type: ignore

from .pkulaw_mcp_catalog import ServiceInfo, ToolInfo, load_catalog


_STATUS_HINTS = {
    400: "参数错误：检查必填参数是否完整",
    401: "认证失败：检查 Bearer Token 是否正确",
    404: "未找到结果：调整查询条件或检查服务地址/条号是否正确",
    429: "请求过于频繁：降低频率后重试",
    500: "服务器内部错误：可联系技术支持",
}

ALLOWED_MCP_SERVICE_NAMES = {
    "法条识别与溯源",
    "检索司法案例-语义",
    "检索司法案例-关键词",
    "检索法律法规-语义",
}


class PkulawMcpClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        add_mcp_suffix: bool = False,
        timeout: int = 120,
        init_params: dict[str, Any] | None = None,
        session: Any | None = None,
    ) -> None:
        if requests is None:
            raise ImportError("Please install `requests` before using PkulawMcpClient.")
        self.base_url = base_url.rstrip("/")
        if add_mcp_suffix and not self.base_url.endswith("/mcp"):
            self.base_url = f"{self.base_url}/mcp"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._timeout = timeout
        self._session = session or requests.Session()
        self._id = 1
        self._initialized = False
        self._tools: list[dict[str, Any]] | None = None
        self._init_params = init_params or {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "pkulaw-mcp-client", "version": "0.1"},
            "capabilities": {},
        }

    def _post(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        self._id += 1
        response = self._session.post(
            self.base_url,
            json=payload,
            headers=self._headers,
            timeout=self._timeout,
        )
        if response.status_code != 200:
            hint = _STATUS_HINTS.get(response.status_code)
            suffix = f" ({hint})" if hint else ""
            raise RuntimeError(
                f"mcp request failed: {response.status_code} {response.text}{suffix}"
            )
        try:
            body = response.json()
        except requests.exceptions.JSONDecodeError:
            body = _parse_event_stream(response)
            if body is None:
                raise RuntimeError(
                    f"mcp response was not JSON or event-stream: "
                    f"content-type={response.headers.get('content-type')} "
                    f"text[:200]={response.text[:200]!r}"
                )
        if "error" in body:
            raise RuntimeError(f"mcp error: {body['error']}")
        return body

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._post("initialize", self._init_params)
        tools_response = self._post("tools/list")
        result = tools_response.get("result") or {}
        self._tools = result.get("tools", [])
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        return list(self._tools or [])

    def call_tool(self, tool_name: str, params: dict[str, Any]) -> Any:
        self._ensure_initialized()
        response = self._post("tools/call", {"name": tool_name, "arguments": params})
        return response.get("result")

    def tool(self, tool_name: str, **params: Any) -> Any:
        return self.call_tool(tool_name, params)

    @classmethod
    def from_catalog(
        cls,
        service_name: str,
        token: str,
        *,
        add_mcp_suffix: bool = False,
        timeout: int = 120,
        init_params: dict[str, Any] | None = None,
        catalog: dict[str, Any] | None = None,
    ) -> "PkulawMcpClient":
        catalog = catalog or load_catalog()
        services: dict[str, ServiceInfo] = catalog["services"]
        info = services.get(service_name)
        if not info or not info.base_url:
            raise ValueError(f"service not found or missing base_url: {service_name}")
        client = cls(
            info.base_url,
            token,
            add_mcp_suffix=add_mcp_suffix,
            timeout=timeout,
            init_params=init_params,
        )
        _attach_tool_methods(client, info.tools)
        return client


def _attach_tool_methods(client: PkulawMcpClient, tools: list[ToolInfo]) -> None:
    for tool in tools:
        if not tool.name.isidentifier():
            continue

        def _make(name: str):
            def _call(self: PkulawMcpClient, **params: Any) -> Any:
                return self.call_tool(name, params)

            return _call

        method = _make(tool.name)
        setattr(client, tool.name, types.MethodType(method, client))


def _parse_event_stream(response: Any) -> dict[str, Any] | None:
    text = response.text
    if "data:" not in text:
        return None
    candidates: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload and payload != "[DONE]":
            candidates.append(payload)
    if not candidates:
        return None
    for payload in reversed(candidates):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            continue
    return None


def call_service_query(
    service_name: str,
    query: str,
    token: str,
    *,
    tool_name: str = "search_article",
    param_name: str | None = None,
    add_mcp_suffix: bool = False,
    timeout: int = 120,
    catalog: dict[str, Any] | None = None,
) -> Any:
    client = PkulawMcpClient.from_catalog(
        service_name,
        token,
        add_mcp_suffix=add_mcp_suffix,
        timeout=timeout,
        catalog=catalog,
    )
    tools = client.list_tools()
    if tools and not any(tool.get("name") == tool_name for tool in tools):
        if len(tools) == 1:
            tool_name = tools[0].get("name") or tool_name
        else:
            raise ValueError(
                f"tool '{tool_name}' not found for service '{service_name}'. "
                f"available tools: {[tool.get('name') for tool in tools]}"
            )
    if param_name is None:
        for tool in tools:
            if tool.get("name") == tool_name:
                schema = tool.get("inputSchema") or {}
                required = schema.get("required") or []
                properties = schema.get("properties") or {}
                if "text" in required or "text" in properties:
                    param_name = "text"
                elif "query" in required or "query" in properties:
                    param_name = "query"
                elif required:
                    param_name = required[0]
                elif properties:
                    param_name = next(iter(properties.keys()))
                break
    if not param_name:
        raise ValueError(f"unable to infer param name for tool: {tool_name}")
    return client.call_tool(tool_name, {param_name: query})


def parse_mcp_result(result: Any) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "is_error": False,
        "items": [],
        "raw": result,
    }
    if isinstance(result, dict):
        if result.get("isError") is True:
            parsed["is_error"] = True

        if "structuredContent" in result:
            structured = result.get("structuredContent") or {}
            items_candidate = (
                structured.get("result")
                or structured.get("items")
                or structured.get("data")
                or []
            )
            normalized = _normalize_items(items_candidate)
            if normalized:
                parsed["items"] = normalized
                return parsed

        content = result.get("content")
        if isinstance(content, list):
            collected: list[Any] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") != "text":
                    continue
                text = str(part.get("text") or "").strip()
                if not text:
                    continue
                collected.extend(_normalize_items(text))
            if collected:
                parsed["items"] = collected
                return parsed

        # 一些服务会直接把结果放在 result/items 字段（不在 structuredContent 内）
        direct_candidate = result.get("result") or result.get("items")
        normalized = _normalize_items(direct_candidate)
        if normalized:
            parsed["items"] = normalized
            return parsed

    return parsed


def _normalize_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            # 文本里经常是多行标题+链接，按行拆分更便于展示
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return lines or [text]
        return _normalize_items(decoded)
    return [value]


def _first_non_empty(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        val = item.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ""


def _is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def format_items_for_display(items: list[Any]) -> str:
    blocks: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = _first_non_empty(
                item,
                ["title", "name", "case_name", "law_name", "docTitle", "doc_name"],
            )
            article = _first_non_empty(
                item,
                ["article", "summary", "snippet", "abstract", "content", "text"],
            )
            url = _first_non_empty(item, ["url", "link", "href", "doc_url"])
            parts = [p for p in [title, article, url] if p]
            if parts:
                blocks.append("\n".join(parts))
            elif item:
                blocks.append(json.dumps(item, ensure_ascii=False))
            continue

        text = str(item).strip()
        if not text:
            continue
        if _is_url(text) and blocks:
            blocks[-1] = f"{blocks[-1]}\n{text}"
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def validate_mcp_service_name(
    service_name: str,
    allowed_service_names: set[str] | None = None,
) -> str:
    allowed = allowed_service_names or ALLOWED_MCP_SERVICE_NAMES
    if service_name not in allowed:
        allowed_str = ", ".join(sorted(allowed))
        raise ValueError(
            f"unsupported mcp service name: {service_name}. allowed: {allowed_str}"
        )
    return service_name


def mcp_query(
    service_name: str,
    query: str,
    token: str | None = None,
    *,
    catalog: dict[str, Any] | None = None,
    allowed_service_names: set[str] | None = None,
) -> str:
    validate_mcp_service_name(service_name, allowed_service_names=allowed_service_names)
    resolved_token = (token or os.getenv("PKULAW_MCP_TOKEN", "")).strip()
    if not resolved_token:
        raise RuntimeError("PKULAW_MCP_TOKEN is required")
    result = call_service_query(
        service_name,
        query,
        resolved_token,
        catalog=catalog,
    )
    parsed = parse_mcp_result(result)
    rendered = format_items_for_display(parsed["items"])
    body = rendered if rendered.strip() else json.dumps(parsed["raw"], ensure_ascii=False, indent=2)
    return f"MCP 服务名: {service_name}\n查询内容: {query}\n\n结果是:\n{body}"
