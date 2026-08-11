# Labour Lawsuit Cowork

当前分支实现了案件级异步劳动争议工作台。每个案件由有界 `asyncio.Queue` 串行推进，Controller 统一调度 Scenario、证据解析、权威检索、确定性规则计算、LegalAnalysis 与文书生成；状态只能通过强类型 `CasePatch` 和 `DomainStateManager` 修改，PostgreSQL 事务同时提交案件版本、快照、投影与严格递增的事件序号。React 工作台只消费 committed state/events，不在浏览器生成业务结论。

用户只与 ControllerAgent 对话；其他 Agent 是按需调用的专业能力，不是并行在线的聊天对象。`Prompt_Template` 中保存角色语义和劳动法场景知识，运行时再从 Pydantic 模型生成精确 JSON Schema，因此模型能看到自己的分工，而代码仍是协议的唯一事实源。

## 本地启动

需要 Python 3.11+ 和 PostgreSQL。仓库提供了可选的本地数据库容器：

```bash
docker compose up -d postgres
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

编辑 `.env`，设置以下独立边界：

- 法律推理：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`
- 视觉 OCR：`OCR_BASE_URL`、`OCR_API_KEY`、`OCR_MODEL`
- 法源 MCP：`PKULAW_MCP_TOKEN`
- 运行时：`DATABASE_URL`、`APP_API_TOKENS_JSON`

OCR 不复用法律推理模型；任一边界缺少配置时启动明确失败。`APP_API_TOKENS_JSON` 是 Bearer token 到 actor ID 的映射，例如 `{"a-long-random-token":"local-user"}`。加载环境变量后启动：

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

证据上传位于 `/api/v1/cases/{case_id}/evidence`，支持 TXT、CSV、JSON、文本/扫描 PDF、DOCX、PNG 和 JPEG。抽取正文与案件快照分离保存；扫描件直接进入独立视觉 OCR 主链。事实确认、规则计算、法律分析与文书生成统一提交 typed command。

API 分为 command、query、evidence 和 SSE Router。事件历史支持 `after_sequence`、`limit` 和 `next_after_sequence`；SSE 的 `id` 是案件内 sequence，支持 `Last-Event-ID`、心跳与重连补发。错误统一返回 `code`、`message`、`retryable`、`case_id`、`command_id` 和 `fields`。

## 验证

```bash
TEST_DATABASE_URL=postgresql+asyncpg://USER@127.0.0.1:5432/TEST_DB \
  python -m unittest discover -s tests -p "test_*.py" -v
ruff check Agents tests utils scripts/run_staging_case.py
mypy Agents utils scripts/run_staging_case.py
alembic upgrade head --sql
cd jobpilot-front && npm run lint && npm run build && npm audit --audit-level=low
```

本机 PostgreSQL 的事务回滚、恢复、幂等、并发、backpressure 和取消验证已经完成。真实外部 staging 必须使用受控测试凭据和去身份化材料：

```bash
export STAGING_API_BASE=http://127.0.0.1:8000/api/v1
export STAGING_API_TOKEN=your-controlled-test-token
python scripts/run_staging_case.py \
  --contract /path/to/deidentified-contract.txt \
  --wage-record /path/to/deidentified-wage.csv \
  --conflicting-wage-record /path/to/deidentified-conflicting-wage.csv \
  --scanned-notice /path/to/deidentified-scanned-notice.png
```

该脚本真实执行追问、材料上传、扫描 OCR、冲突确认、法源检索、规则计算、法律分析、仲裁申请书、版本更新和 SSE 断线续传；任何外部失败都会直接终止，不生成替代结论。旧同步 `Agents.api_server`、`MultiAgentSessionService` 与 `/api/v1/sessions` 已删除，业务状态只有异步案件主链能够写入。
