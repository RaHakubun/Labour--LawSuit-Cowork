# Labour Lawsuit Cowork

`lbw` 当前主线是完整的案件级异步劳动争议工作台。每个案件由有界 `asyncio.Queue` 串行推进，Controller 统一调度 Scenario、证据解析、权威检索、确定性规则计算、LegalAnalysis 与文书生成；状态只能通过强类型 `CasePatch` 和 `DomainStateManager` 修改，Postgres 事务同时提交案件版本、快照、投影与严格递增的事件序号。React 工作台只消费 committed state/events，不在浏览器生成业务结论。

## 本地启动

需要 Python 3.11+ 和 PostgreSQL。仓库提供了可选的本地数据库容器：

```bash
docker compose up -d postgres
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

编辑 `.env`，至少设置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`PKULAW_MCP_TOKEN`、`DATABASE_URL` 和 `APP_API_TOKENS_JSON`。`APP_API_TOKENS_JSON` 是 Bearer token 到 actor ID 的映射，例如 `{"a-long-random-token":"local-user"}`。加载环境变量后启动：

```bash
set -a
source .env
set +a
./start_project.sh
```

启动脚本会先执行 `alembic upgrade head`，再以单 Uvicorn worker 启动 `Agents.async_api:app`，并启动 `jobpilot-front`。在实现跨进程命令认领前不要增加 worker 数量。

## API 主链

所有案件接口都需要 `Authorization: Bearer <token>`：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cases \
  -H "Authorization: Bearer a-long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"role_id":"worker"}'
```

提交用户消息时必须提供幂等键和当前案件版本：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cases/CASE_ID/commands \
  -H "Authorization: Bearer a-long-random-token" \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key":"browser-message-1",
    "expected_case_version":0,
    "payload":{
      "command_type":"submit_user_message",
      "text":"公司以绩效不合格为由口头辞退我，没有给书面通知。"
    }
  }'
```

证据上传位于 `/api/v1/cases/{case_id}/evidence`，支持 TXT、CSV、JSON、PDF 和 DOCX；事实确认、规则计算、法律分析与文书生成统一提交 typed command。事件流位于 `/api/v1/cases/{case_id}/events`，审计历史位于 `/api/v1/cases/{case_id}/events/history`，产物列表和版本内容位于 `/api/v1/cases/{case_id}/artifacts`。SSE 的 `id` 是案件内 sequence；重连时从最后 sequence 补发已提交事件。

## 验证

```bash
python -m unittest discover -s tests -p "test_*.py" -v
ruff check Agents tests
mypy Agents/domain Agents/application Agents/runtime Agents/infrastructure Agents/api Agents/async_api.py
alembic upgrade head --sql
```

旧同步 `Agents.api_server`、`MultiAgentSessionService` 与 `/api/v1/sessions` 已删除，业务状态只有异步案件主链能够写入。代码里程碑以 `ASYNC_CASE_RUNTIME_SPEC.md` 为准；真实 PostgreSQL 故障注入与外部 LLM/MCP staging 需要在具备对应服务和受控测试凭据的部署环境执行。
