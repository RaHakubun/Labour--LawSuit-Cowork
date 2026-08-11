# 案件级异步劳动争议工作台集成报告

> 更新时间：2026-08-11
>
> 分支：`codex/complete-case-runtime`（对齐 `lbw` / `ff1e422`）
>
> 判定原则：只记录实际代码与验证证据；外部服务未真实调用时保持未完成。

## 1. 当前结论

案件级异步架构代码已经完成。生产入口只有 `Agents.async_api:app`，不存在旧同步 `/sessions`、双写服务、兼容业务链、dummy handler 或外部失败后的法律结论 fallback。

本机 PostgreSQL、Runtime、API 和前端门禁已经通过。真实法律推理 LLM、法源 MCP 和视觉 OCR staging 尚未执行，因为当前环境未配置受控测试凭据；这是一项明确的发布前外部验收，不以 mock 或协议样本替代。

## 2. 架构落地矩阵

| 能力 | 状态 | 实现与证据 |
|---|---|---|
| schema 2.0 与迁移 | 已完成 | 强类型 pending confirmation、证据抽取、事实—证据链接、备注与缺失信息；未知版本拒绝 |
| 单一 Controller 编排 | 已完成 | 六类 decision + `CaseOrchestrator`；显式命令归一为确定性 decision |
| StateManager | 已完成 | producer 权限、迁移、引用完整性、冲突、patch 原子性、依赖级 stale |
| 命令生命周期 | 已完成 | accepted/running/completed/failed/cancelled；accepted 命令唯一终态 |
| Runtime 可靠性 | 已完成 | 同案串行、跨案并发、增量批次广播、取消、backpressure、恢复、空闲回收、shutdown |
| PostgreSQL | 已完成 | 行锁、幂等唯一约束、snapshot/event/projection 同事务、消息/证据/产物投影、Alembic v2 |
| 证据与 OCR | 代码完成 | 原件/正文/哈希/解析元数据/真实性风险分离；TXT/CSV/JSON/DOCX/PDF/PNG/JPEG；独立视觉 OCR |
| ToolHub 与法源 | 代码完成 | typed result/error；仅连接、429、明确 5xx 重试；协议/鉴权错误立即失败 |
| 规则、分析与文书 | 已完成 | 可追溯规则输入、受控 Legal context、争议焦点、法律分析报告和仲裁申请书版本链 |
| API | 已完成 | command/query/evidence/SSE Router；统一错误体；事件游标分页；Last-Event-ID 与心跳 |
| React 工作台 | 已完成 | 三角色、完整历史、pending、冲突、证据状态、法源、规则、失败事件与文书版本；无 350ms 轮询 |
| 真实外部 staging | 未完成 | 缺少本轮受控 LLM/MCP/OCR 凭据；不得标记为通过 |

## 3. 验证记录

### 后端

- 70 项 `unittest` 通过。
- 使用本机 PostgreSQL 17.5 测试库执行真实事务回滚测试。
- 覆盖幂等、恢复、并发顺序、队列容量、取消、patch rejection 与事件终态。
- Ruff 全仓通过。
- Mypy `Agents` 与 `utils` 通过。
- Alembic v2 upgrade、downgrade、再次 upgrade 与 offline SQL 已通过。

### 前端

- ESLint 通过。
- Vite 8.2.1 production build 通过。
- `npm audit --audit-level=low`：0 vulnerabilities。
- 桌面与 390px 移动视口完成浏览器渲染检查，控制台无 error/warning。

### 外部边界

- LLM/MCP/OCR 的 typed 协议解析和错误分类由审校协议样本覆盖。
- 这些合约测试不是 staging 完成证明。
- 当前 `LLM_*`、`OCR_*`、`PKULAW_MCP_TOKEN` 与 staging API 配置均未设置，所以没有生成外部成功报告。

## 4. 发布前唯一未完成门禁

- [ ] 使用受控测试账号真实调用法律推理 LLM。
- [ ] 使用受控测试账号真实调用法源 MCP，并验证鉴权失败和超时。
- [ ] 使用受控视觉模型处理去身份化扫描解除材料，并验证 OCR 失败。
- [ ] 完整执行追问 → 场景路由 → 合同/工资/扫描证据 → 冲突确认 → 法源 → 赔偿规则 → 分析 → 仲裁申请书 → 版本更新 → SSE 重连。
- [ ] 在 staging 分别注入 LLM 协议错误、数据库事务中断和客户端断线，确认无无依据产物。

执行入口：

```bash
export STAGING_API_BASE=https://controlled-staging.example/api/v1
export STAGING_API_TOKEN=managed-test-token
python scripts/run_staging_case.py \
  --contract /secure/deidentified-contract.txt \
  --wage-record /secure/deidentified-wage.csv \
  --conflicting-wage-record /secure/deidentified-conflicting-wage.csv \
  --scanned-notice /secure/deidentified-scanned-notice.png
```

脚本通过真实 SSE 等待命令终态，并主动断开一次后携带 `Last-Event-ID` 重连。缺少法源、规则结果、两类产物、第二版产物或重连证据时直接失败。

## 5. 安全结论

- 当前分支未新增密钥；`.env`、证据存储和运行产物保持忽略。
- OCR 与法律推理使用独立配置，不静默复用模型。
- 旧历史中的疑似密钥继续按已泄露处理；按既定决策不重写 Git 历史，账户所有者仍需完成撤销。
- 真实材料必须先去身份化，凭据只进入本地 `.env` 或 secret manager。
