import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  BriefcaseBusiness,
  Calculator,
  Check,
  ChevronRight,
  FileCheck2,
  FileText,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Scale,
  Send,
  ShieldCheck,
  Upload,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_AGENT_API_BASE || "http://127.0.0.1:8000/api/v1";

const stageLabels = {
  intake: "案件建立",
  fact_collecting: "事实收集",
  evidence_processing: "证据处理",
  analysis_ready: "等待分析",
  analyzing: "法律分析中",
  document_ready: "文书就绪",
  completed: "已完成",
};

const eventLabels = {
  "command.accepted": "任务已进入案件队列",
  "operation.completed": "本次处理已完成",
  "operation.cancelled": "本次处理已取消",
  "operation.failed": "处理失败，请查看提示",
  "clarification.requested": "需要补充案件事实",
  "evidence.registered": "证据已安全登记",
  "evidence.parsed": "证据解析完成",
  "fact.confirmed": "事实已由你确认",
  "tool.call_started": "正在检索权威法源",
  "tool.call_completed": "权威法源检索完成",
  "analysis.updated": "法律分析已更新",
  "artifact.generated": "新文书版本已生成",
};

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("labour-token") || "");
  const [draftToken, setDraftToken] = useState(token);
  const [cases, setCases] = useState([]);
  const [activeCase, setActiveCase] = useState(null);
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const [activeOperation, setActiveOperation] = useState(null);
  const [error, setError] = useState("");

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
      const detail = body.detail;
      throw new Error(typeof detail === "string" ? detail : detail?.message || detail?.code || "请求失败");
    }
    return body;
  }, [token]);

  const waitForCommand = useCallback(async (caseId, commandId) => {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const history = await api(`/cases/${caseId}/events/history`);
      const terminal = history.events.find((event) =>
        event.command_id === commandId &&
        ["operation.completed", "operation.failed"].includes(event.event_type));
      if (terminal?.event_type === "operation.completed") return terminal;
      if (terminal?.event_type === "operation.cancelled") {
        throw new Error("案件任务已取消");
      }
      if (terminal?.event_type === "operation.failed") {
        throw new Error(terminal.payload.message || "案件任务执行失败");
      }
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
    throw new Error("案件任务执行超时，可稍后通过事件记录继续查看");
  }, [api]);

  const refreshCases = useCallback(async () => {
    if (!token) return;
    const body = await api("/cases");
    setCases(body.cases);
    setActiveCase((current) => {
      if (!current) return body.cases[0] || null;
      return body.cases.find((item) => item.case_id === current.case_id) || current;
    });
  }, [api, token]);

  const refreshActive = useCallback(async () => {
    if (!activeCase?.case_id) return;
    const current = await api(`/cases/${activeCase.case_id}`);
    setActiveCase(current);
    setCases((items) => items.map((item) => item.case_id === current.case_id ? current : item));
  }, [activeCase?.case_id, api]);

  useEffect(() => {
    refreshCases().catch((reason) => setError(reason.message));
  }, [refreshCases]);

  useCaseEventStream({
    caseId: activeCase?.case_id,
    token,
    onEvent: useCallback((event) => {
      setEvents((items) => [...items.filter((item) => item.event_id !== event.event_id), event].slice(-80));
      if (["operation.completed", "operation.failed", "patch.applied"].includes(event.event_type)) {
        refreshActive().catch((reason) => setError(reason.message));
      }
    }, [refreshActive]),
  });

  async function run(action) {
    setBusy(true);
    setError("");
    try {
      await action();
      await refreshActive();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy(false);
    }
  }

  async function createCase() {
    const created = await api("/cases", { method: "POST", body: JSON.stringify({ role_id: "worker" }) });
    setCases((items) => [created, ...items]);
    setActiveCase(created);
    setEvents([]);
  }

  async function command(payload) {
    if (!activeCase) return;
    const accepted = await api(`/cases/${activeCase.case_id}/commands`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: `${payload.command_type}-${crypto.randomUUID()}`,
        expected_case_version: activeCase.version,
        payload,
      }),
    });
    setActiveOperation(accepted.command_id);
    try {
      await waitForCommand(activeCase.case_id, accepted.command_id);
    } finally {
      setActiveOperation(null);
    }
  }

  async function cancelActiveOperation() {
    if (!activeCase || !activeOperation) return;
    await api(`/cases/${activeCase.case_id}/commands`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: `cancel-${activeOperation}`,
        expected_case_version: activeCase.version,
        payload: { command_type: "cancel_operation", operation_id: activeOperation },
      }),
    });
  }

  function connect() {
    localStorage.setItem("labour-token", draftToken.trim());
    setToken(draftToken.trim());
    setError("");
  }

  if (!token) {
    return <ConnectScreen value={draftToken} onChange={setDraftToken} onConnect={connect} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><Scale size={25} /><div><strong>劳动争议工作台</strong><span>Case Intelligence</span></div></div>
        <button className="new-case" onClick={() => run(createCase)}><Plus size={17} /> 新建案件</button>
        <div className="case-list-label">我的案件</div>
        <div className="case-list">
          {cases.map((item) => (
            <button key={item.case_id} className={activeCase?.case_id === item.case_id ? "case-item active" : "case-item"} onClick={() => { setActiveCase(item); setEvents([]); }}>
              <span className="case-dot" /><span><b>{item.current_goal || "待梳理的劳动争议"}</b><small>{stageLabels[item.stage]} · 版本 {item.version}</small></span><ChevronRight size={15} />
            </button>
          ))}
        </div>
        <div className="security-note"><ShieldCheck size={17} /><span>案件按所有者隔离<br />状态与事件全程留痕</span></div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div><span className="eyebrow">当前案件</span><h1>{activeCase?.current_goal || "开始建立你的案件事实"}</h1></div>
          {activeCase && <div className="stage-pill"><span className="pulse" />{stageLabels[activeCase.stage]}</div>}
        </header>
        {error && <div className="error-banner"><AlertTriangle size={17} />{error}</div>}
        {!activeCase ? (
          <EmptyCase onCreate={() => run(createCase)} />
        ) : (
          <div className="case-grid">
            <section className="conversation panel">
              <PanelTitle icon={MessageSquareText} title="案件对话" meta="由 Controller 统一协调" />
              <Conversation events={events} pendingQuestions={activeCase.pending_questions} />
              <Composer disabled={busy} onSend={(text) => run(() => command({ command_type: "submit_user_message", text }))} />
            </section>

            <div className="right-column">
              <section className="panel">
                <PanelTitle icon={FileCheck2} title="事实与确认" meta={`${activeCase.candidate_facts.length} 项事实`} />
                <Facts facts={activeCase.candidate_facts} conflicts={activeCase.fact_conflicts} disabled={busy} onConfirm={(fact, conflictId) => run(() => command({ command_type: "confirm_fact", fact_id: fact.fact_id, value: fact.value, conflict_id: conflictId }))} />
              </section>
              <section className="panel">
                <PanelTitle icon={Upload} title="证据材料" meta="本地隔离存储 · SHA-256" />
                <Evidence items={activeCase.evidence} disabled={busy} onUpload={(file) => run(async () => {
                  const form = new FormData(); form.append("file", file);
                  const accepted = await api(`/cases/${activeCase.case_id}/evidence?idempotency_key=evidence-${crypto.randomUUID()}&expected_case_version=${activeCase.version}`, { method: "POST", body: form });
                  setActiveOperation(accepted.command_id);
                  try {
                    await waitForCommand(activeCase.case_id, accepted.command_id);
                  } finally {
                    setActiveOperation(null);
                  }
                })} />
              </section>
              <section className="panel">
                <PanelTitle icon={BookOpen} title="争议焦点与法源" meta={`${activeCase.authorities.length} 条权威来源`} />
                <Issues issues={activeCase.issues} authorities={activeCase.authorities} />
              </section>
              <section className="panel action-panel">
                <PanelTitle icon={BriefcaseBusiness} title="计算与产物" meta="每次生成保留版本" />
                <Actions activeCase={activeCase} busy={busy} onCommand={(payload) => run(() => command(payload))} onOpen={async (id) => api(`/cases/${activeCase.case_id}/artifacts/${id}`)} />
              </section>
            </div>
          </div>
        )}
      </main>
      {busy && <div className="busy-indicator"><LoaderCircle className="spin" size={18} /> 正在执行案件任务 {activeOperation && <button onClick={() => cancelActiveOperation().catch((reason) => setError(reason.message))}>取消</button>}</div>}
    </div>
  );
}

function useCaseEventStream({ caseId, token, onEvent }) {
  const lastSequence = useRef(0);
  useEffect(() => {
    lastSequence.current = 0;
    if (!caseId || !token) return undefined;
    const controller = new AbortController();
    let retryTimer;
    async function connect() {
      try {
        const response = await fetch(`${API_BASE}/cases/${caseId}/events?after_sequence=${lastSequence.current}`, { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
        if (!response.ok || !response.body) throw new Error("事件连接失败");
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
            lastSequence.current = Math.max(lastSequence.current, event.sequence);
            onEvent(event);
          }
        }
      } catch {
        if (!controller.signal.aborted) retryTimer = window.setTimeout(connect, 1500);
      }
    }
    connect();
    return () => { controller.abort(); window.clearTimeout(retryTimer); };
  }, [caseId, token, onEvent]);
}

function ConnectScreen({ value, onChange, onConnect }) {
  return <div className="connect-screen"><div className="connect-card"><div className="connect-mark"><Scale size={34} /></div><span className="eyebrow">LABOUR CASE INTELLIGENCE</span><h1>把零散经历，整理成可追溯的案件。</h1><p>输入服务端为你配置的访问令牌。令牌只保存在当前浏览器，用于隔离你的案件、证据与文书。</p><label>访问令牌<input type="password" value={value} onChange={(event) => onChange(event.target.value)} placeholder="Bearer token" onKeyDown={(event) => event.key === "Enter" && onConnect()} /></label><button onClick={onConnect}>进入案件工作台 <ChevronRight size={17} /></button></div></div>;
}

function EmptyCase({ onCreate }) { return <div className="empty-state"><div className="empty-icon"><Scale size={30} /></div><h2>从一个具体争议开始</h2><p>新建案件后，Controller 会先梳理事实，再调度证据解析、权威检索、规则计算和文书生成。</p><button onClick={onCreate}><Plus size={17} /> 新建劳动争议案件</button></div>; }
function PanelTitle({ icon: Icon, title, meta }) { return <div className="panel-title"><div><Icon size={18} /><h2>{title}</h2></div><span>{meta}</span></div>; }

function Conversation({ events, pendingQuestions }) {
  const visible = events.filter((event) => event.visibility === "user" && eventLabels[event.event_type]);
  return <div className="conversation-body">{visible.length === 0 && <div className="conversation-intro"><Scale size={22} /><p>请直接描述发生了什么、你希望解决什么。系统会对缺失事实进行追问，不会替你虚构案情。</p></div>}{visible.map((event) => <div key={event.event_id} className={`timeline-event ${event.event_type === "operation.failed" ? "failed" : ""}`}><span /><div><b>{eventLabels[event.event_type]}</b><small>{event.event_type === "clarification.requested" ? event.payload.question || event.payload.questions?.map((item) => item.text).join("；") : event.payload.summary || event.payload.message || "已写入案件审计记录"}</small></div></div>)}{pendingQuestions.map((item) => <div className="question-card" key={item.question_id}><b>还需要你确认</b><p>{item.text}</p></div>)}</div>;
}

function Composer({ disabled, onSend }) { const [text, setText] = useState(""); return <form className="composer" onSubmit={(event) => { event.preventDefault(); const value = text.trim(); if (!value) return; onSend(value); setText(""); }}><textarea value={text} onChange={(event) => setText(event.target.value)} placeholder="例如：公司在 7 月 20 日以绩效不合格为由口头辞退我，没有书面通知……" /><button disabled={disabled || !text.trim()}><Send size={17} /> 发送</button></form>; }

function Facts({ facts, conflicts, disabled, onConfirm }) { return <div className="stack-list">{facts.length === 0 && <Muted text="尚未形成候选事实" />}{facts.map((fact) => <div className="fact-row" key={fact.fact_id}><div><b>{humanizeFact(fact.fact_id)}</b><span>{String(fact.value)}</span><small className={`status ${fact.status}`}>{fact.status === "confirmed" ? "已确认" : fact.status === "disputed" ? "存在冲突" : "待确认"}</small></div>{fact.status !== "confirmed" && fact.status !== "disputed" && <button disabled={disabled} onClick={() => onConfirm(fact)}><Check size={14} /> 确认</button>}</div>)}{conflicts.map((conflict) => <div className="conflict-card" key={conflict.conflict_id}><b>事实冲突：{humanizeFact(conflict.fact_id)}</b><p>已有：{String(conflict.existing.value)} · 新材料：{String(conflict.incoming.value)}</p><div><button onClick={() => onConfirm(conflict.existing, conflict.conflict_id)}>采用已有值</button><button onClick={() => onConfirm(conflict.incoming, conflict.conflict_id)}>采用新值</button></div></div>)}</div>; }

function Evidence({ items, disabled, onUpload }) { return <div><label className="upload-box"><Upload size={20} /><span><b>上传劳动合同、工资材料或解除通知</b><small>支持 TXT、CSV、JSON、PDF、DOCX，单文件不超过 20 MB</small></span><input type="file" accept=".txt,.csv,.json,.pdf,.docx" disabled={disabled} onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.target.value = ""; }} /></label><div className="evidence-list">{items.map((item) => <div key={item.evidence_id}><FileText size={17} /><span><b>{item.display_name}</b><small>{item.status === "parsed" ? `已解析 · ${item.linked_fact_ids.length} 个事实链接` : "等待解析"}</small></span></div>)}</div></div>; }

function Issues({ issues, authorities }) { return <div className="issues-layout"><div>{issues.length === 0 ? <Muted text="生成法律分析后，争议焦点会显示在这里" /> : issues.map((item) => <article className="issue-card" key={item.issue_id}><b>{item.title}</b><p>{item.conclusion}</p><small>{item.fact_ids.length} 项事实 · {item.authority_ids.length} 条法源</small></article>)}</div><div className="authority-list">{authorities.slice(0, 4).map((item) => <a key={item.authority_id} href={item.source_url || undefined} target="_blank" rel="noreferrer"><BookOpen size={15} /><span><b>{item.title || item.source_id}</b><small>{item.tool_name}</small></span></a>)}</div></div>; }

function Actions({ activeCase, busy, onCommand, onOpen }) { const wage = activeCase.candidate_facts.find((item) => item.fact_id === "employment.monthly_wage")?.value; const [artifact, setArtifact] = useState(null); return <div className="actions-wrap"><div className="action-buttons"><button disabled={busy || !wage} onClick={() => onCommand({ command_type: "calculate_rule", calc_type: "wage_base", inputs: { monthly_wage: wage }, fact_ids: ["employment.monthly_wage"] })}><Calculator size={16} /> 计算工资基数</button><button disabled={busy || activeCase.authorities.length === 0} onClick={() => onCommand({ command_type: "request_analysis" })}><Scale size={16} /> 生成法律分析</button><button disabled={busy || activeCase.issues.length === 0} onClick={() => onCommand({ command_type: "request_document", document_type: "labour_arbitration_application" })}><FileText size={16} /> 生成仲裁申请书</button></div><div className="artifact-list">{activeCase.artifacts.map((item) => <button key={item.artifact_id} onClick={async () => setArtifact(await onOpen(item.artifact_id))}><span className={item.stale ? "artifact-icon stale" : "artifact-icon"}><FileText size={17} /></span><span><b>{item.title}</b><small>{item.stale ? "案件已变化 · 建议重新生成" : `共 ${item.revision_count} 个版本`}</small></span><ChevronRight size={15} /></button>)}</div>{artifact && <div className="document-view"><div><b>{artifact.title}</b><button onClick={() => setArtifact(null)}>关闭</button></div><pre>{artifact.revisions.at(-1)?.content}</pre></div>}</div>; }

function Muted({ text }) { return <div className="muted"><FileCheck2 size={17} />{text}</div>; }
function humanizeFact(id) { return ({ "employment.monthly_wage": "月工资", "employment.start_date": "入职日期", "termination.date": "解除日期", "termination.written_notice": "书面解除通知", "parties.employer": "用人单位", "parties.employee": "劳动者" })[id] || id; }

export default App;
