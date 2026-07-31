# Labour Lawsuit Cowork

`lbw` 当前主线是案件级异步事件流后端。每个案件由一个有界 `asyncio.Queue` 串行推进，`CaseRuntime.run()` 以 async generator 产出事件；状态只能通过强类型 `CasePatch` 和 `DomainStateManager` 修改，Postgres 事务同时提交案件版本、快照与严格递增的事件序号。Controller 使用异步 OpenAI-compatible client，API 使用 Bearer token 映射案件所有者，SSE 支持 `Last-Event-ID` 续传。

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

启动脚本会先执行 `alembic upgrade head`，再以单 Uvicorn worker 启动 `Agents.async_api:app`。在实现跨进程命令认领前不要增加 worker 数量。当前 `lbw` 不包含 `jobpilot-front`，因此启动脚本会明确只启动后端。

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

事件流位于 `/api/v1/cases/{case_id}/events`，审计历史位于 `/api/v1/cases/{case_id}/events/history`。SSE 的 `id` 是案件内 sequence；重连时发送 `Last-Event-ID` 即可补发已提交事件。

## 验证

```bash
python -m unittest discover -s tests -p "test_*.py" -v
ruff check Agents tests
mypy Agents/domain Agents/application Agents/runtime Agents/infrastructure Agents/api Agents/async_api.py
alembic upgrade head --sql
```

旧 `Agents.api_server` 与 `/api/v1/sessions` 仍保留为迁移期兼容代码，但不再是默认启动主链。后续 Scenario、证据、规则、LegalAnalysis、文书和前端工作的唯一计划基线是 `ASYNC_CASE_RUNTIME_SPEC.md`。
