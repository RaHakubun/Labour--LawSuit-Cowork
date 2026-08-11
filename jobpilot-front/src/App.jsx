import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  BriefcaseBusiness,
  Calculator,
  Check,
  ChevronRight,
  CircleDot,
  FileCheck2,
  FileText,
  Landmark,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Radio,
  Scale,
  Send,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_AGENT_API_BASE || "http://127.0.0.1:8000/api/v1";
const TERMINAL_EVENTS = new Set([
  "operation.completed",
  "operation.failed",
  "operation.cancelled",
]);
const PROJECTION_EVENTS = new Set([
  "clarification.requested",
  "evidence.registered",
  "evidence.parsed",
  "fact.confirmation_requested",
  "fact.confirmed",
  "analysis.updated",
  "artifact.generated",
  ...TERMINAL_EVENTS,
]);

const stageLabels = {
  intake: "案件建立",
  fact_collecting: "事实收集",
  evidence_processing: "证据处理",
  analysis_ready: "等待分析",
  analyzing: "法律分析中",
  document_ready: "文书就绪",
  completed: "已完成",
};
const roleLabels = { worker: "劳动者", lawyer: "代理律师", employer: "用人单位" };
const eventLabels = {
  "command.accepted": "任务已进入案件队列",
  "command.rejected": "任务未被案件队列接受",
  "operation.started": "案件任务开始执行",
  "operation.completed": "本次处理已完成",
  "operation.cancelled": "本次处理已取消",
  "operation.failed": "案件任务执行失败",
  "message.received": "已记录你的陈述",
  "clarification.requested": "需要补充案件事实",
  "fact.confirmation_requested": "候选事实等待确认",
  "evidence.registered": "证据已登记",
  "evidence.parsed": "证据解析完成",
  "fact.confirmed": "事实已确认",
  "tool.call_started": "正在检索权威法源",
  "tool.call_completed": "权威法源检索完成",
  "tool.call_failed": "外部工具调用失败",
  "patch.rejected": "案件状态更新被拒绝",
  "analysis.updated": "法律分析已更新",
  "artifact.generated": "新文书版本已生成",
};

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("labour-token") || "");
  const [draftToken, setDraftToken] = useState(token);
  const [cases, setCases] = useState([]);
  const [activeCase, setActiveCase] = useState(null);
  const [eventsByCase, setEventsByCase] = useState({});
  const [activeOperation, setActiveOperation] = useState(null);
  const [requestPending, setRequestPending] = useState(false);
  const [streamState, setStreamState] = useState("offline");
  const [error, setError] = useState("");
  const [showNewCase, setShowNewCase] = useState(false);
  const terminalWaiters = useRef(new Map());
  const terminalCache = useRef(new Map());
  const busy = requestPending || Boolean(activeOperation);

  const api = useCallback(async (path, options = {}) => {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...options.headers,
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const failure = new Error(body.message || body.code || "请求失败");
      failure.code = body.code;
      failure.retryable = body.retryable;
      failure.fields = body.fields;
      throw failure;
    }
    return body;
  }, [token]);

  const refreshCase = useCallback(async (caseId) => {
    if (!caseId) return;
    const current = await api(`/cases/${caseId}`);
    setCases((items) => items.map((item) => item.case_id === current.case_id ? current : item));
    setActiveCase((item) => item?.case_id === current.case_id ? current : item);
  }, [api]);

  const refreshCases = useCallback(async () => {
    if (!token) return;
    const body = await api("/cases");
    setCases(body.cases);
    setActiveCase((current) => {
      if (!current) return body.cases[0] || null;
      return body.cases.find((item) => item.case_id === current.case_id) || body.cases[0] || null;
    });
  }, [api, token]);

  useEffect(() => {
    let active = true;
    window.queueMicrotask(() => {
      if (active) refreshCases().catch((reason) => setError(reason.message));
    });
    return () => { active = false; };
  }, [refreshCases]);

  const settleTerminal = useCallback((event) => {
    terminalCache.current.set(event.command_id, event);
    const waiter = terminalWaiters.current.get(event.command_id);
    if (!waiter) return;
    terminalWaiters.current.delete(event.command_id);
    window.clearTimeout(waiter.timeout);
    if (event.event_type === "operation.completed") waiter.resolve(event);
    else waiter.reject(new Error(event.payload.message || event.payload.code || "案件任务未完成"));
  }, []);

  const onEvent = useCallback((event) => {
    setEventsByCase((current) => {
      const existing = current[event.case_id] || [];
      const next = [...existing.filter((item) => item.event_id !== event.event_id), event]
        .sort((left, right) => left.sequence - right.sequence);
      return { ...current, [event.case_id]: next };
    });
    if (TERMINAL_EVENTS.has(event.event_type)) settleTerminal(event);
    if (PROJECTION_EVENTS.has(event.event_type)) {
      refreshCase(event.case_id).catch((reason) => setError(reason.message));
    }
  }, [refreshCase, settleTerminal]);

  const streamCaseId = activeOperation?.caseId || activeCase?.case_id;
  useCaseEventStream({
    caseId: streamCaseId,
    token,
    onEvent,
    onStateChange: setStreamState,
  });

  const awaitTerminal = useCallback((commandId) => {
    const cached = terminalCache.current.get(commandId);
    if (cached) {
      return cached.event_type === "operation.completed"
        ? Promise.resolve(cached)
        : Promise.reject(new Error(cached.payload.message || cached.payload.code));
    }
    return new Promise((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        terminalWaiters.current.delete(commandId);
        reject(new Error("实时事件等待超时；案件仍可在事件记录中继续追踪"));
      }, 120_000);
      terminalWaiters.current.set(commandId, { resolve, reject, timeout });
    });
  }, []);

  async function run(action) {
    setRequestPending(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setRequestPending(false);
    }
  }

  async function createCase(roleId) {
    const created = await api("/cases", {
      method: "POST",
      body: JSON.stringify({ role_id: roleId }),
    });
    setCases((items) => [created, ...items]);
    setActiveCase(created);
    setShowNewCase(false);
  }

  async function command(payload) {
    if (!activeCase) return;
    const caseId = activeCase.case_id;
    const accepted = await api(`/cases/${caseId}/commands`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: `${payload.command_type}-${crypto.randomUUID()}`,
        expected_case_version: activeCase.version,
        payload,
      }),
    });
    setActiveOperation({ caseId, commandId: accepted.command_id });
    try {
      await awaitTerminal(accepted.command_id);
    } finally {
      setActiveOperation((current) => current?.commandId === accepted.command_id ? null : current);
    }
  }

  async function uploadEvidence(file) {
    if (!activeCase) return;
    const caseId = activeCase.case_id;
    const form = new FormData();
    form.append("file", file);
    const accepted = await api(
      `/cases/${caseId}/evidence?idempotency_key=evidence-${crypto.randomUUID()}&expected_case_version=${activeCase.version}`,
      { method: "POST", body: form },
    );
    setActiveOperation({ caseId, commandId: accepted.command_id });
    try {
      await awaitTerminal(accepted.command_id);
    } finally {
      setActiveOperation((current) => current?.commandId === accepted.command_id ? null : current);
    }
  }

  async function cancelActiveOperation() {
    if (!activeOperation) return;
    const sourceCase = cases.find((item) => item.case_id === activeOperation.caseId);
    await api(`/cases/${activeOperation.caseId}/commands`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: `cancel-${activeOperation.commandId}`,
        expected_case_version: sourceCase?.version || 0,
        payload: {
          command_type: "cancel_operation",
          operation_id: activeOperation.commandId,
        },
      }),
    });
  }

  function connect() {
    const nextToken = draftToken.trim();
    if (!nextToken) return;
    localStorage.setItem("labour-token", nextToken);
    setToken(nextToken);
    setError("");
  }

  if (!token) {
    return <ConnectScreen value={draftToken} onChange={setDraftToken} onConnect={connect} />;
  }

  const activeEvents = activeCase ? eventsByCase[activeCase.case_id] || [] : [];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-seal"><Scale size={21} /></span>
          <div><strong>劳争案卷</strong><span>CASE LEDGER · 02</span></div>
        </div>
        <button className="new-case" onClick={() => setShowNewCase(true)}>
          <Plus size={16} /> 建立新案卷
        </button>
        <div className="case-list-label">案卷目录</div>
        <div className="case-list">
          {cases.map((item, index) => (
            <button
              key={item.case_id}
              className={activeCase?.case_id === item.case_id ? "case-item active" : "case-item"}
              onClick={() => setActiveCase(item)}
            >
              <span className="case-index">{String(index + 1).padStart(2, "0")}</span>
              <span><b>{item.current_goal || "待梳理的劳动争议"}</b><small>{roleLabels[item.role_id]} · {stageLabels[item.stage]} · v{item.version}</small></span>
              <ChevronRight size={14} />
            </button>
          ))}
        </div>
        <div className="security-note"><ShieldCheck size={16} /><span>所有者隔离<br />事实、证据、版本全程留痕</span></div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">CASE / {activeCase?.case_id?.slice(0, 8) || "NEW"}</span>
            <h1>{activeCase?.current_goal || "建立一份可追溯的劳动争议案卷"}</h1>
            {activeCase && <p>{roleLabels[activeCase.role_id]}视角 · 当前由 {activeCase.active_agent} 处理</p>}
          </div>
          {activeCase && <div className="case-status"><span className="stage-pill"><CircleDot size={13} />{stageLabels[activeCase.stage]}</span><span className={`stream-state ${streamState}`}><Radio size={12} />{streamState === "online" ? "事件在线" : streamState === "reconnecting" ? "正在重连" : "事件离线"}</span></div>}
        </header>
        {error && <div className="error-banner"><AlertTriangle size={17} /><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}

        {!activeCase ? (
          <EmptyCase onCreate={() => setShowNewCase(true)} />
        ) : (
          <div className="case-grid">
            <section className="conversation panel">
              <PanelTitle icon={MessageSquareText} title="案件口述与进程" meta={`${activeEvents.length} 条可见事件`} />
              <Conversation
                events={activeEvents}
                pendingQuestions={activeCase.pending_questions}
                pendingConfirmation={activeCase.pending_confirmation}
                missingInformation={activeCase.missing_information}
              />
              <Composer disabled={busy} onSend={(text) => run(() => command({ command_type: "submit_user_message", text }))} />
            </section>

            <div className="right-column">
              <section className="panel facts-panel">
                <PanelTitle icon={FileCheck2} title="事实底稿" meta={`${activeCase.candidate_facts.length} 项`} />
                <Facts
                  facts={activeCase.candidate_facts}
                  conflicts={activeCase.fact_conflicts}
                  disabled={busy}
                  onConfirm={(fact, conflictId) => run(() => command({ command_type: "confirm_fact", fact_id: fact.fact_id, value: fact.value, conflict_id: conflictId }))}
                />
              </section>
              <section className="panel">
                <PanelTitle icon={Upload} title="证据目录" meta="原件与抽取正文分离" />
                <Evidence items={activeCase.evidence} disabled={busy} onUpload={(file) => run(() => uploadEvidence(file))} />
              </section>
              <section className="panel analysis-panel">
                <PanelTitle icon={Landmark} title="法源与争议焦点" meta={`${activeCase.authorities.length} 条法源`} />
                <Issues issues={activeCase.issues} authorities={activeCase.authorities} />
              </section>
              <section className="panel action-panel">
                <PanelTitle icon={BriefcaseBusiness} title="规则计算与文书版本" meta={`${activeCase.artifacts.length} 项产物`} />
                <Actions
                  activeCase={activeCase}
                  busy={busy}
                  onCommand={(payload) => run(() => command(payload))}
                  onOpen={(id) => api(`/cases/${activeCase.case_id}/artifacts/${id}`)}
                />
              </section>
            </div>
          </div>
        )}
      </main>

      {showNewCase && <NewCaseDialog onClose={() => setShowNewCase(false)} onCreate={(role) => run(() => createCase(role))} busy={requestPending} />}
      {activeOperation && <div className="busy-indicator"><LoaderCircle className="spin" size={17} /><span>案卷任务执行中<small>{activeOperation.commandId.slice(0, 8)}</small></span><button onClick={() => cancelActiveOperation().catch((reason) => setError(reason.message))}>取消任务</button></div>}
    </div>
  );
}

function useCaseEventStream({ caseId, token, onEvent, onStateChange }) {
  const sequences = useRef(new Map());
  useEffect(() => {
    if (!caseId || !token) {
      onStateChange("offline");
      return undefined;
    }
    const controller = new AbortController();
    let retryTimer;
    async function connect() {
      const sequence = sequences.current.get(caseId) || 0;
      onStateChange(sequence ? "reconnecting" : "connecting");
      try {
        const response = await fetch(`${API_BASE}/cases/${caseId}/events?after_sequence=${sequence}`, {
          headers: { Authorization: `Bearer ${token}`, "Last-Event-ID": String(sequence) },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error("事件连接失败");
        onStateChange("online");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() || "";
          for (const block of blocks) {
            const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
            if (!dataLine) continue;
            const event = JSON.parse(dataLine.slice(6));
            sequences.current.set(caseId, Math.max(sequences.current.get(caseId) || 0, event.sequence));
            onEvent(event);
          }
        }
        if (!controller.signal.aborted) retryTimer = window.setTimeout(connect, 1200);
      } catch {
        if (!controller.signal.aborted) {
          onStateChange("reconnecting");
          retryTimer = window.setTimeout(connect, 1500);
        }
      }
    }
    connect();
    return () => {
      controller.abort();
      window.clearTimeout(retryTimer);
    };
  }, [caseId, token, onEvent, onStateChange]);
}

function ConnectScreen({ value, onChange, onConnect }) {
  return <div className="connect-screen"><div className="connect-folio"><span>卷宗编号</span><b>LABOUR / CASE / RUNTIME</b><i /></div><div className="connect-card"><div className="connect-mark"><Scale size={31} /></div><span className="eyebrow">案件级异步劳动争议工作台</span><h1>让每一个结论，<br />都能回到事实与证据。</h1><p>输入服务端配置的访问令牌。令牌仅保存在当前浏览器，用于隔离你的案件、证据、分析与文书版本。</p><label>访问令牌<input type="password" value={value} onChange={(event) => onChange(event.target.value)} placeholder="Bearer token" onKeyDown={(event) => event.key === "Enter" && onConnect()} /></label><button onClick={onConnect}>启封案卷 <ChevronRight size={17} /></button></div></div>;
}

function NewCaseDialog({ onClose, onCreate, busy }) {
  const [role, setRole] = useState("worker");
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="new-case-title"><button className="dialog-close" onClick={onClose}><X size={17} /></button><span className="eyebrow">NEW CASE FILE</span><h2 id="new-case-title">选择本案工作视角</h2><p>角色决定场景路由与提问方式，不会改变证据和法源的可追溯要求。</p><div className="role-options">{Object.entries(roleLabels).map(([id, label]) => <button key={id} className={role === id ? "selected" : ""} onClick={() => setRole(id)}><span>{id === "worker" ? "劳" : id === "lawyer" ? "律" : "企"}</span><b>{label}</b><small>{id === "worker" ? "梳理诉求与举证" : id === "lawyer" ? "代理审查与成文" : "合规应诉与风险核对"}</small></button>)}</div><button className="dialog-primary" disabled={busy} onClick={() => onCreate(role)}>{busy ? "正在建立…" : "建立案卷"}</button></div></div>;
}

function EmptyCase({ onCreate }) { return <div className="empty-state"><span className="folio-number">卷 / 〇〇</span><div className="empty-icon"><Scale size={29} /></div><h2>从一项具体争议开始</h2><p>Controller 将按事实确认、证据解析、法源检索、规则计算、法律分析与文书版本的单一主链推进。</p><button onClick={onCreate}><Plus size={17} /> 建立劳动争议案卷</button></div>; }
function PanelTitle({ icon: Icon, title, meta }) { return <div className="panel-title"><div><Icon size={17} /><h2>{title}</h2></div><span>{meta}</span></div>; }

function Conversation({ events, pendingQuestions, pendingConfirmation, missingInformation }) {
  const visible = events.filter((event) => event.visibility === "user" && (eventLabels[event.event_type] || event.event_type === "message.received"));
  return <div className="conversation-body">{visible.length === 0 && <div className="conversation-intro"><Scale size={21} /><p>请按时间顺序描述发生了什么、希望解决什么。系统会追问缺失事实，不会替你虚构案情或补写外部工具失败后的结论。</p></div>}{visible.map((event) => event.event_type === "message.received" ? <div key={event.event_id} className="user-message"><small>你的陈述 · #{event.sequence}</small><p>{event.payload.text}</p></div> : <div key={event.event_id} className={`timeline-event ${["operation.failed", "tool.call_failed", "patch.rejected", "command.rejected"].includes(event.event_type) ? "failed" : ""}`}><span /><div><b>{eventLabels[event.event_type] || event.event_type}</b><small>{eventDetail(event)}</small><em>#{event.sequence}</em></div></div>)}{pendingConfirmation && <div className="question-card confirmation"><b>等待事实确认</b><p>{pendingConfirmation.prompt}</p></div>}{pendingQuestions.map((item) => <div className="question-card" key={item.question_id}><b>待补信息</b><p>{item.text}</p></div>)}{missingInformation.map((item) => <div className="question-card missing" key={item.information_id}><b>{item.blocking ? "阻塞信息" : "补充信息"}</b><p>{item.description}</p></div>)}</div>;
}

function eventDetail(event) { if (event.event_type === "clarification.requested") return event.payload.question || event.payload.questions?.map((item) => item.text).join("；") || "请查看待补信息"; return event.payload.summary || event.payload.message || event.payload.code || "已写入不可变事件记录"; }
function Composer({ disabled, onSend }) { const [message, setMessage] = useState(""); return <form className="composer" onSubmit={(event) => { event.preventDefault(); const value = message.trim(); if (!value) return; onSend(value); setMessage(""); }}><textarea value={message} onChange={(event) => setMessage(event.target.value)} disabled={disabled} placeholder="按时间、人物、行为、结果描述，例如：公司在 7 月 20 日口头通知解除……" /><div><small>事实将先进入待确认状态</small><button disabled={disabled || !message.trim()}><Send size={16} /> 提交陈述</button></div></form>; }

function Facts({ facts, conflicts, disabled, onConfirm }) { return <div className="stack-list">{facts.length === 0 && <Muted text="尚未形成候选事实" />}{facts.map((fact) => <div className="fact-row" key={fact.fact_id}><div><b>{humanizeFact(fact.fact_id)}</b><span>{String(fact.value)}</span><small className={`status ${fact.status}`}>{fact.status === "confirmed" ? "已确认" : fact.status === "disputed" ? "存在冲突" : "待确认"}</small></div>{fact.status !== "confirmed" && fact.status !== "disputed" && <button disabled={disabled} onClick={() => onConfirm(fact)}><Check size={13} /> 确认</button>}</div>)}{conflicts.map((conflict) => <div className="conflict-card" key={conflict.conflict_id}><b>冲突 / {humanizeFact(conflict.fact_id)}</b><p>已有：{String(conflict.existing.value)}<br />新材料：{String(conflict.incoming.value)}</p><div><button disabled={disabled} onClick={() => onConfirm(conflict.existing, conflict.conflict_id)}>采用已有值</button><button disabled={disabled} onClick={() => onConfirm(conflict.incoming, conflict.conflict_id)}>采用新值</button></div></div>)}</div>; }

function Evidence({ items, disabled, onUpload }) { const statusText = { registered: "已登记，等待解析", parsing: "正在解析", parsed: "已解析", failed: "解析失败" }; return <div><label className="upload-box"><Upload size={19} /><span><b>添加合同、工资、解除或沟通材料</b><small>TXT / CSV / JSON / PDF / DOCX / PNG / JPEG · 最大 20 MB</small></span><input type="file" accept=".txt,.csv,.json,.pdf,.docx,.png,.jpg,.jpeg" disabled={disabled} onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.target.value = ""; }} /></label><div className="evidence-list">{items.length === 0 && <Muted text="尚未登记证据" />}{items.map((item) => <div key={item.evidence_id} className={`evidence-item ${item.status}`}><FileText size={16} /><span><b>{item.display_name}</b><small>{statusText[item.status] || item.status}{item.status === "parsed" ? ` · ${item.linked_fact_ids.length} 个事实链接` : ""}</small></span><em>{item.sha256.slice(0, 8)}</em></div>)}</div></div>; }

function Issues({ issues, authorities }) { return <div className="issues-layout"><div>{issues.length === 0 ? <Muted text="法律分析后显示争议焦点" /> : issues.map((item, index) => <article className="issue-card" key={item.issue_id}><span>焦点 {String(index + 1).padStart(2, "0")}</span><b>{item.title}</b><p>{item.conclusion}</p><small>{item.fact_ids.length} 项事实 · {item.evidence_ids.length} 份证据 · {item.authority_ids.length} 条法源</small></article>)}</div><div className="authority-list">{authorities.length === 0 && <Muted text="尚无法源记录" />}{authorities.map((item) => <a key={item.authority_id} href={item.source_url || undefined} target="_blank" rel="noreferrer"><BookOpen size={14} /><span><b>{item.title || item.source_id}</b><small>{item.tool_name} · {item.parsed_status}</small></span></a>)}</div></div>; }

function Actions({ activeCase, busy, onCommand, onOpen }) {
  const wageFact = activeCase.candidate_facts.find((item) => item.fact_id === "employment.monthly_wage");
  const [artifact, setArtifact] = useState(null);
  const [revisionIndex, setRevisionIndex] = useState(-1);
  const revision = artifact?.revisions.at(revisionIndex);
  return <div className="actions-wrap"><div className="action-buttons"><button disabled={busy || wageFact?.status !== "confirmed"} onClick={() => onCommand({ command_type: "calculate_rule", calc_type: "wage_base", inputs: { monthly_wage: wageFact.value }, fact_ids: ["employment.monthly_wage"] })}><Calculator size={15} /> 计算工资基数</button><button disabled={busy || activeCase.authorities.length === 0} onClick={() => onCommand({ command_type: "request_analysis" })}><Scale size={15} /> 形成法律分析</button><button disabled={busy || activeCase.issues.length === 0} onClick={() => onCommand({ command_type: "request_document", document_type: "labour_arbitration_application" })}><FileText size={15} /> 生成仲裁申请书</button></div>{activeCase.rule_results.length > 0 && <div className="rule-strip">{activeCase.rule_results.map((item) => <div key={item.result_id}><span>{item.rule}</span><b>{formatRuleResult(item.result)} {item.unit}</b><small>基于 v{item.input_case_version} · {item.fact_ids.length} 项事实</small></div>)}</div>}<div className="artifact-list">{activeCase.artifacts.length === 0 && <Muted text="尚未生成分析或文书" />}{activeCase.artifacts.map((item) => <button key={item.artifact_id} onClick={async () => { setArtifact(await onOpen(item.artifact_id)); setRevisionIndex(-1); }}><span className={item.stale ? "artifact-icon stale" : "artifact-icon"}><FileText size={16} /></span><span><b>{item.title}</b><small>{item.stale ? "关联事实已变化 · 产物过期" : `${item.revision_count} 个受控版本`}</small></span><ChevronRight size={14} /></button>)}</div>{artifact && <div className="document-view"><div className="document-head"><span><b>{artifact.title}</b><small>{artifact.artifact_type}</small></span><button onClick={() => setArtifact(null)}><X size={15} /></button></div><div className="revision-tabs">{artifact.revisions.map((item, index) => <button key={item.revision} className={item === revision ? "active" : ""} onClick={() => setRevisionIndex(index)}>版本 {item.revision}</button>)}</div><pre>{revision?.content}</pre><div className="document-trace">引用 {revision?.fact_ids.length || 0} 项事实 · {revision?.evidence_ids.length || 0} 份证据 · {revision?.authority_ids.length || 0} 条法源 · {revision?.rule_result_ids.length || 0} 个规则结果</div></div>}</div>;
}

function formatRuleResult(result) { const value = result.amount ?? result.wage_base ?? result.value; return value === undefined ? JSON.stringify(result) : String(value); }
function Muted({ text }) { return <div className="muted"><FileCheck2 size={16} />{text}</div>; }
function humanizeFact(id) { return ({ "employment.monthly_wage": "月工资", "employment.start_date": "入职日期", "termination.date": "解除日期", "termination.written_notice": "书面解除通知", "parties.employer": "用人单位", "parties.employee": "劳动者" })[id] || id; }

export default App;
