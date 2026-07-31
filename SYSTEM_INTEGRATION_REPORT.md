# 劳动法多 Agent 系统前后端一体化报告

> 历史报告，已停止作为完成度基线。本文描述的 `jobpilot-front`、RAG 和若干集成完成项与当前 `lbw` 文件树不一致；自 2026-07-31 起，实施与验收以 `ASYNC_CASE_RUNTIME_SPEC.md`、实际代码和自动化验证结果为准。

## 1. 报告目的
- 明确当前 `jobpilot-front` 与现有后端 Agent/工具能力的真实匹配度。
- 识别所有阻断上线的系统级问题。
- 固化一份可执行、可验收、可持续追踪的改造方案。
- 作为后续开发的唯一进度基线，持续更新本文件的状态项。

## 2. 调查范围
- 前端：`jobpilot-front/src` 全量组件、数据流、状态管理。
- 后端：`Agents/`、`utils/`、`Prompt_Template/`、`Example/`、`tests/`。
- 路由链路：`ControllerAgent -> ScenarioAgent -> LegalAnalysisAgent`。
- 工具链路：MCP 工具调用与劳动法规则计算器。

## 3. 核心结论
1. 前端主对话链路已完成后端真实接入，不再走本地 mock 主路径。
2. 后端会话 API、三段式状态机、handoff 事件、会话持久化均已落地并验证通过。
3. 三身份模块已接入后端约束：`role_id` 与 `module_key` 会进行一致性校验。
4. 用工单位三模块（合规扫描/合同模板/沟通指南）已接入后端直连服务。
5. 模板协议审计通过，`scene_id.enum` 与后端场景目录保持一致。

## 4. 当前能力矩阵

### 4.1 前端能力
- 身份选择：已具备（3 角色）。
- 模块入口：已具备（9 模块）。
- 自动模块匹配：已具备（关键词匹配）。
- 溯源与步骤展示 UI：已具备（但数据来源主要为本地/Mock）。
- 会话历史：仅元数据，无法恢复完整消息链。
- 消息生成：本地生成，无后端请求。

### 4.2 后端能力
- Controller/Scenario/Legal 三段式编排：已具备。
- `askmore` 循环机制：已具备。
- MCP 调用与历史注入：已具备。
- 劳动法规则计算器：已具备（含省市社平工资 3 倍封顶逻辑）。
- 会话服务化接口：已具备。
- 前端渲染协议：已具备。
- 控制权移交事件协议：已具备（前端可视化）。

### 4.3 前端 9 模块与后端覆盖
- `compensation_calculator`：已接入（worker 模块路由 + 会话链路）。
- `evidence_checker`：已接入（worker 模块路由 + 场景链路）。
- `strategy_advisor`：已接入（worker 模块路由 + 场景链路）。
- `compliance_scanner`：已接入（employer 直连模块服务）。
- `contract_templates`：已接入（employer 直连模块服务）。
- `communication_guide`：已接入（employer 直连模块服务）。
- `law_search`：已接入（lawyer 模块路由 + 场景链路）。
- `evidence_organizer`：已接入（lawyer 模块路由 + 场景链路）。
- `lawyer_compensation`：已接入（lawyer 模块路由 + 会话链路）。

## 5. P0/P1 问题清单

### 5.1 P0（必须优先清零）
1. 无。

### 5.2 P1（高优先）
1. 附件仅元数据，尚未落地文件内容解析链路。
2. 前端 ESLint 仍有历史规则告警（不阻塞 build）。

## 6. 目标架构（成熟化）
- `Frontend`：纯展示与交互。
- `Conversation API`：会话创建、消息推进、历史读取、事件流。
- `Orchestrator`：Controller/Scenario/Legal 状态机。
- `Tool Layer`：MCP + 劳动法计算器 + 合同/沟通能力服务。
- `Presentation Adapter`：把后端结构转为前端渲染块，禁止裸 JSON。
- `Storage`：会话、消息、事件、工具调用、审计日志。
- `Observability`：状态迁移、错误暴露、链路追踪。

## 7. 强约束开发原则
1. 不做“最小可用”降配方案。
2. 不做静默 fallback；协议不满足必须暴露错误。
3. 不做 dummy 用例掩盖问题；所有失败项必须可复现可定位。
4. 前端对话区禁止裸 JSON/裸结构数据展示。
5. Agent 控制权移交必须在前端可视化。
6. 每次开发后更新本文件“进度检查表”。

## 8. 分阶段实施计划

### 阶段 A：协议冻结与模型统一
- 定义会话状态机、消息协议、事件协议、渲染块协议。
- 固化场景目录、角色目录与模块映射。
- 增加模板协议审计，暴露 scene_id 异常。

### 阶段 B：后端服务化
- 新增会话 API。
- 将编排器从控制台模式升级为服务模式。
- 输出结构化事件（包括 handoff）。

### 阶段 C：前端数据层重构
- 删除本地业务生成路径。
- 全量切换至后端会话 API。
- 增加“当前接管 Agent”与“handoff 横幅”展示。

### 阶段 D：模块能力补齐
- 劳动者/律师链路全接入。
- 用工单位缺口能力补齐（合规、模板、沟通）。
- MCP 与计算器结果统一结构化呈现。

### 阶段 E：全链路质量与验收
- 端到端场景压测与回归。
- 协议一致性与状态迁移测试。
- 错误暴露机制验收。

## 9. 进度检查表（唯一追踪）

### 阶段 A：协议冻结与模型统一
- [x] A1 形成系统分析报告并落地根目录
- [x] A2 建立可维护的场景目录代码常量
- [x] A3 建立前端展示适配层代码骨架（禁止裸 JSON 直出）
- [x] A4 建立模板协议审计脚本并产出异常报告
- [x] A5 固化会话/消息/事件契约文档

### 阶段 B：后端服务化
- [x] B1 会话 API 设计与实现
- [x] B2 状态机编排服务化
- [x] B3 handoff 事件输出标准化
- [x] B4 会话与事件持久化

### 阶段 C：前端数据层重构
- [x] C1 移除本地 `generateModuleResponse` 主路径
- [x] C2 接入会话 API 与事件流
- [x] C3 实现 Agent 接管态可视化
- [x] C4 实现无裸数据渲染规范

### 阶段 D：模块能力补齐
- [x] D1 劳动者三模块后端全接入
- [x] D2 律师三模块后端全接入
- [x] D3 用工单位三模块后端能力补齐
- [x] D4 工具结果卡片化与可追溯展示

### 阶段 E：质量验收
- [x] E1 端到端主链路验收
- [x] E2 协议一致性验收
- [x] E3 错误暴露与审计验收
- [x] E4 发布前回归验收

## 10. 当前阻断提醒
- 无 P0 阻断。
- 当前剩余改进项：附件文件内容解析链路、前端历史 ESLint 规则治理。

## 11. 最近进展记录
- 2026-04-10：新增 `Agents/scene_catalog.py`，统一场景、角色、模块映射及校验接口。
- 2026-04-10：新增 `Agents/presentation_adapter.py`，将 Agent 输出转为前端可渲染块，避免裸 JSON 直接渲染。
- 2026-04-10：新增 `scripts/audit_prompt_templates.py` 与 `PROMPT_TEMPLATE_AUDIT.md`，并已将模板 `scene_id.enum` 审计项全部修复为一致。
- 2026-04-10：新增 `Agents/conversation_contract.py`，固化会话、消息、handoff 事件数据结构与校验规则。
- 2026-04-10：新增 `scripts/check_integration_progress.py`，可对本报告打勾项做自动化进度检查。
- 2026-04-10：新增 `Agents/session_service.py` 服务化状态机编排，支持 `Controller -> Scenario -> Legal` 全链路会话推进。
- 2026-04-10：新增 `Agents/api_server.py` 会话 API（`/sessions`、`/turns`、`/messages`、`/health`），输出统一消息与 handoff 结构。
- 2026-04-10：为 `MultiAgentSessionService` 增加文件快照持久化（`storage/sessions/*.json`）及重启恢复加载能力。
- 2026-04-10：新增 `tests/test_api_server.py` 与会话持久化测试，当前 `python -m unittest discover -s tests -p 'test_*.py' -v` 全量通过。
- 2026-04-10：前端新增 `jobpilot-front/src/services/conversationApi.js` 与 `responseAdapter.js`，`ChatPanel` 已切换为真实会话 API，移除本地 mock 生成主路径。
- 2026-04-10：前端新增 handoff 横幅与当前发言 Agent 标签，用户可见控制权移交过程且不渲染裸 JSON。
- 2026-04-11：新增 `Agents/module_services.py` 并接入 `session_service`，用工单位三模块可走后端直连能力（`EmployerModuleAgent`）。
- 2026-04-11：`session_service` 新增 `module_key` + `role_id` 一致性校验，worker/lawyer 模块会注入场景提示并进入主链路。
- 2026-04-11：`presentation_adapter` 新增工具结果卡片化输出（`tool_result_card`），提升工具结果可读性与可追溯性。
- 2026-04-11：`api_server` 增加 `ValueError -> 400` 统一错误暴露；新增模块路由相关测试后，`python -m unittest discover -s tests -p 'test_*.py' -v` 通过（75/75）。
- 2026-04-11：前端 `npm run build` 成功，通过生产构建回归。
