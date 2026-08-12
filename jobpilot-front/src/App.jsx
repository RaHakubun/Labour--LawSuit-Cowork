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
  KeyRound,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Scale,
  Send,
  Settings2,
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
  const [cases, setCases] = useState([]);
  const [activeCase, setActiveCase] = useState(null);
  const [eventsByCase, setEventsByCase] = useState({});
  const [activeOperation, setActiveOperation] = useState(null);
  const [requestPending, setRequestPending] = useState(false);
  const [streamState, setStreamState] = useState("offline");
  const [error, setError] = useState("");
  const [showNewCase, setShowNewCase] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState("");
  const [integrationStatuses, setIntegrationStatuses] = useState([]);
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
    setCases((items) => items.map((item) => (
      item.case_id === current.case_id ? current : item
    )));
    setActiveCase((item) => (item?.case_id === current.case_id ? current : item));
  }, [api]);

  const refreshCases = useCallback(async () => {
    if (!token) return;
    const body = await api("/cases");
    setCases(body.cases);
    setActiveCase((current) => {
      if (!current) return body.cases[0] || null;
      return body.cases.find((item) => item.case_id === current.case_id)
        || body.cases[0]
        || null;
    });
  }, [api, token]);

  const refreshIntegrations = useCallback(async () => {
    if (!token) {
      setIntegrationStatuses([]);
      return;
    }
    const body = await api("/integrations");
    setIntegrationStatuses(body.integrations);
  }, [api, token]);

  useEffect(() => {
    let active = true;
    window.queueMicrotask(() => {
      if (active && token) {
        Promise.all([refreshCases(), refreshIntegrations()])
          .catch((reason) => setError(reason.message));
      }
    });
    return () => { active = false; };
  }, [refreshCases, refreshIntegrations, token]);

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

  function requestNewCase() {
    if (!token) {
      setSettingsNotice("新建案件前，请先填写工作台访问令牌。");
      setShowSettings(true);
      return;
    }
    setShowNewCase(true);
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
      setActiveOperation((current) => (
        current?.commandId === accepted.command_id ? null : current
      ));
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
      setActiveOperation((current) => (
        current?.commandId === accepted.command_id ? null : current
      ));
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

  async function saveSettings({ accessToken, integrations }) {
    const nextToken = accessToken.trim();
    if (!nextToken) throw new Error("请先填写工作台访问令牌");
    const request = async (path, options = {}) => {
      const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
          Authorization: `Bearer ${nextToken}`,
          "Content-Type": "application/json",
          ...options.headers,
        },
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const failure = new Error(body.message || body.code || "配置保存失败");
        failure.code = body.code;
        throw failure;
      }
      return body;
    };

    const casesBody = await request("/cases");
    const currentStatuses = await request("/integrations");
    const statusMap = Object.fromEntries(
      currentStatuses.integrations.map((item) => [item.provider, item]),
    );
    for (const item of integrations) {
      const current = statusMap[item.provider];
      const endpoint = item.endpoint?.trim() || current?.endpoint || null;
      const model = item.model?.trim() || current?.model || null;
      const secret = item.secret.trim() || null;
      const metadataChanged = current?.configured
        && (endpoint !== (current.endpoint || null) || model !== (current.model || null));
      if (!secret && !metadataChanged) continue;
      await request(`/integrations/${item.provider}`, {
        method: "PUT",
        body: JSON.stringify({ endpoint, model, secret }),
      });
    }

    const updatedStatuses = await request("/integrations");
    localStorage.setItem("labour-token", nextToken);
    setToken(nextToken);
    setIntegrationStatuses(updatedStatuses.integrations);
    setCases(casesBody.cases);
    setActiveCase((current) => (
      casesBody.cases.find((item) => item.case_id === current?.case_id)
      || casesBody.cases[0]
      || null
    ));
    setError("");
  }

  async function deleteIntegration(provider) {
    await api(`/integrations/${provider}`, { method: "DELETE" });
    await refreshIntegrations();
  }

  const activeEvents = activeCase ? eventsByCase[activeCase.case_id] || [] : [];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Scale size={25} />
          <div>
            <strong>劳动争议工作台</strong>
            <span>Case Intelligence</span>
          </div>
        </div>
        <button className="new-case" onClick={requestNewCase}>
          <Plus size={17} /> 新建案件
        </button>
        <div className="case-list-label">我的案件</div>
        <div className="case-list">
          {cases.map((item) => (
            <button
              key={item.case_id}
              className={activeCase?.case_id === item.case_id ? "case-item active" : "case-item"}
              onClick={() => setActiveCase(item)}
            >
              <span className="case-dot" />
              <span>
                <b>{item.current_goal || "待梳理的劳动争议"}</b>
                <small>{roleLabels[item.role_id]} · {stageLabels[item.stage]} · 版本 {item.version}</small>
              </span>
              <ChevronRight size={15} />
            </button>
          ))}
          {cases.length === 0 && (
            <div className="case-list-empty">尚无案件，可先浏览右侧工作台功能。</div>
          )}
        </div>
        <div className="security-note">
          <ShieldCheck size={17} />
          <span>案件按所有者隔离<br />事实、证据、版本全程留痕</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <span className="eyebrow">{activeCase ? "当前案件" : "工作台概览"}</span>
            <h1>{activeCase?.current_goal || "开始建立你的案件事实"}</h1>
            {activeCase && (
              <p>{roleLabels[activeCase.role_id]}视角 · 当前由 {activeCase.active_agent} 处理</p>
            )}
          </div>
          <div className="topbar-actions">
            {activeCase && (
              <div className="stage-pill">
                <span className={`pulse ${streamState}`} />
                {stageLabels[activeCase.stage]}
                <small>{streamLabel(streamState)}</small>
              </div>
            )}
            <button
              className="settings-trigger"
              onClick={() => {
                setSettingsNotice("");
                setShowSettings(true);
              }}
            >
              <Settings2 size={15} /> 设置
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <AlertTriangle size={17} />
            <span>{error}</span>
            <button onClick={() => setError("")} aria-label="关闭错误提示"><X size={15} /></button>
          </div>
        )}

        {activeCase ? (
          <CaseWorkspace
            activeCase={activeCase}
            events={activeEvents}
            busy={busy}
            onSend={(text) => run(() => command({ command_type: "submit_user_message", text }))}
            onConfirm={(fact, conflictId) => run(() => command({
              command_type: "confirm_fact",
              fact_id: fact.fact_id,
              value: fact.value,
              conflict_id: conflictId,
            }))}
            onUpload={(file) => run(() => uploadEvidence(file))}
            onCommand={(payload) => run(() => command(payload))}
            onOpen={(id) => api(`/cases/${activeCase.case_id}/artifacts/${id}`)}
          />
        ) : (
          <BrowseWorkspace connected={Boolean(token)} />
        )}
      </main>

      {showNewCase && (
        <NewCaseDialog
          onClose={() => setShowNewCase(false)}
          onCreate={(role) => run(() => createCase(role))}
          busy={requestPending}
        />
      )}
      {showSettings && (
        <SettingsDrawer
          token={token}
          statuses={integrationStatuses}
          notice={settingsNotice}
          onClose={() => {
            setShowSettings(false);
            setSettingsNotice("");
          }}
          onSave={async (payload) => {
            await saveSettings(payload);
            setShowSettings(false);
            setSettingsNotice("");
          }}
          onDelete={deleteIntegration}
        />
      )}
      {activeOperation && (
        <div className="busy-indicator">
          <LoaderCircle className="spin" size={18} />
          <span>正在执行案件任务<small>{activeOperation.commandId.slice(0, 8)}</small></span>
          <button onClick={() => cancelActiveOperation().catch((reason) => setError(reason.message))}>
            取消
          </button>
        </div>
      )}
    </div>
  );
}

function CaseWorkspace({
  activeCase,
  events,
  busy,
  onSend,
  onConfirm,
  onUpload,
  onCommand,
  onOpen,
}) {
  return (
    <div className="case-grid">
      <section className="conversation panel">
        <PanelTitle icon={MessageSquareText} title="案件对话" meta="由 Controller 统一协调" />
        <Conversation
          events={events}
          pendingQuestions={activeCase.pending_questions}
          pendingConfirmation={activeCase.pending_confirmation}
          missingInformation={activeCase.missing_information}
        />
        <Composer disabled={busy} onSend={onSend} />
      </section>

      <div className="right-column">
        <section className="panel">
          <PanelTitle
            icon={FileCheck2}
            title="事实与确认"
            meta={`${activeCase.candidate_facts.length} 项事实`}
          />
          <Facts
            facts={activeCase.candidate_facts}
            conflicts={activeCase.fact_conflicts}
            disabled={busy}
            onConfirm={onConfirm}
          />
        </section>
        <section className="panel">
          <PanelTitle icon={Upload} title="证据材料" meta="本地隔离存储 · SHA-256" />
          <Evidence items={activeCase.evidence} disabled={busy} onUpload={onUpload} />
        </section>
        <section className="panel">
          <PanelTitle
            icon={BookOpen}
            title="争议焦点与法源"
            meta={`${activeCase.authorities.length} 条权威来源`}
          />
          <Issues issues={activeCase.issues} authorities={activeCase.authorities} />
        </section>
        <section className="panel action-panel">
          <PanelTitle icon={BriefcaseBusiness} title="计算与产物" meta="每次生成保留版本" />
          <Actions
            activeCase={activeCase}
            busy={busy}
            onCommand={onCommand}
            onOpen={onOpen}
          />
        </section>
      </div>
    </div>
  );
}

function BrowseWorkspace({ connected }) {
  return (
    <div className="case-grid browse-workspace" aria-label="工作台功能概览">
      <section className="conversation panel">
        <PanelTitle icon={MessageSquareText} title="案件对话" meta="由 Controller 统一协调" />
        <div className="conversation-body">
          <div className="conversation-intro">
            <Scale size={22} />
            <p>在这里按时间描述争议经过。Controller 会追问缺失事实，并调度证据、法源、计算、分析和文书能力。</p>
          </div>
          <Muted text="建立案件后，对话与 committed 事件会显示在这里" />
        </div>
        <Composer disabled disabledHint="建立案件后可提交陈述" onSend={() => {}} />
      </section>

      <div className="right-column">
        <section className="panel">
          <PanelTitle icon={FileCheck2} title="事实与确认" meta="候选事实与冲突" />
          <FeatureEmpty
            title="确认关键案件事实"
            text="系统提取的候选事实、证据支持关系和冲突会在这里等待确认。"
          />
        </section>
        <section className="panel">
          <PanelTitle icon={Upload} title="证据材料" meta="原件、正文与哈希分离" />
          <Evidence items={[]} disabled onUpload={() => {}} />
        </section>
        <section className="panel">
          <PanelTitle icon={BookOpen} title="争议焦点与法源" meta="真实来源可追溯" />
          <FeatureEmpty
            title="查看争议焦点与权威依据"
            text="法律分析形成后，这里会展示真实法源、事实引用和证据引用。"
          />
        </section>
        <section className="panel action-panel">
          <PanelTitle icon={BriefcaseBusiness} title="计算与产物" meta="规则结果与文书版本" />
          <BrowseActions connected={connected} />
        </section>
      </div>
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
        const response = await fetch(
          `${API_BASE}/cases/${caseId}/events?after_sequence=${sequence}`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
              "Last-Event-ID": String(sequence),
            },
            signal: controller.signal,
          },
        );
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
            sequences.current.set(
              caseId,
              Math.max(sequences.current.get(caseId) || 0, event.sequence),
            );
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

function NewCaseDialog({ onClose, onCreate, busy }) {
  const [role, setRole] = useState("worker");
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="new-case-title">
        <button className="dialog-close" onClick={onClose} aria-label="关闭新建案件">
          <X size={17} />
        </button>
        <span className="eyebrow">新建案件</span>
        <h2 id="new-case-title">选择本案工作视角</h2>
        <p>角色决定场景路由与提问方式，不改变事实、证据和法源的可追溯要求。</p>
        <div className="role-options">
          {Object.entries(roleLabels).map(([id, label]) => (
            <button
              key={id}
              className={role === id ? "selected" : ""}
              onClick={() => setRole(id)}
            >
              <span>{id === "worker" ? "劳" : id === "lawyer" ? "律" : "企"}</span>
              <b>{label}</b>
              <small>
                {id === "worker"
                  ? "梳理诉求与举证"
                  : id === "lawyer"
                    ? "代理审查与成文"
                    : "合规应诉与风险核对"}
              </small>
            </button>
          ))}
        </div>
        <button className="dialog-primary" disabled={busy} onClick={() => onCreate(role)}>
          {busy ? "正在建立…" : "建立案件"}
        </button>
      </div>
    </div>
  );
}

function SettingsDrawer({ token, statuses, notice, onClose, onSave, onDelete }) {
  const statusMap = Object.fromEntries(statuses.map((item) => [item.provider, item]));
  const [accessToken, setAccessToken] = useState(token);
  const [forms, setForms] = useState(() => ({
    llm: {
      endpoint: statusMap.llm?.endpoint || "",
      model: statusMap.llm?.model || "",
      secret: "",
    },
    ocr: {
      endpoint: statusMap.ocr?.endpoint || "",
      model: statusMap.ocr?.model || "",
      secret: "",
    },
    mcp: { endpoint: "", model: "", secret: "" },
  }));
  const [saving, setSaving] = useState(false);
  const [settingsError, setSettingsError] = useState("");

  const update = (provider, field, value) => setForms((current) => ({
    ...current,
    [provider]: { ...current[provider], [field]: value },
  }));

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setSettingsError("");
    try {
      await onSave({
        accessToken,
        integrations: Object.entries(forms).map(([provider, value]) => ({
          provider,
          ...value,
        })),
      });
    } catch (reason) {
      setSettingsError(settingsErrorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function remove(provider) {
    setSaving(true);
    setSettingsError("");
    try {
      await onDelete(provider);
    } catch (reason) {
      setSettingsError(settingsErrorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="settings-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}
    >
      <aside className="settings-drawer" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header>
          <div>
            <span className="eyebrow">工作台设置</span>
            <h2 id="settings-title">连接与外部服务</h2>
            <p>访问令牌只保存在当前浏览器；服务密钥提交到本机后端并加密存入 PostgreSQL，保存后不会返回浏览器。</p>
          </div>
          <button onClick={onClose} disabled={saving} aria-label="关闭设置">
            <X size={18} />
          </button>
        </header>

        <form onSubmit={submit}>
          {notice && <div className="settings-notice">{notice}</div>}
          {settingsError && (
            <div className="settings-error">
              <AlertTriangle size={15} />
              <span>{settingsError}</span>
            </div>
          )}

          <section className="settings-section access-section">
            <div className="settings-section-title">
              <KeyRound size={16} />
              <span>
                <b>工作台访问令牌</b>
                <small>用于案件所有者鉴权，仅保存在当前浏览器</small>
              </span>
            </div>
            <input
              type="password"
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              placeholder="Bearer token"
              autoComplete="off"
            />
          </section>

          {[
            ["llm", "法律推理 LLM", "OpenAI-compatible endpoint"],
            ["ocr", "视觉 OCR", "独立视觉模型 endpoint"],
            ["mcp", "北大法宝 MCP", "只保存访问 token"],
          ].map(([provider, title, hint]) => {
            const status = statusMap[provider];
            return (
              <section className="settings-section" key={provider}>
                <div className="settings-section-title">
                  <span className={`config-dot ${status?.configured ? "configured" : ""}`} />
                  <span>
                    <b>{title}</b>
                    <small>
                      {status?.configured
                        ? `已配置${status.updated_at ? ` · ${new Date(status.updated_at).toLocaleString()}` : ""}`
                        : hint}
                    </small>
                  </span>
                  {status?.configured && (
                    <button
                      type="button"
                      className="remove-config"
                      disabled={saving}
                      onClick={() => remove(provider)}
                    >
                      移除
                    </button>
                  )}
                </div>
                {provider !== "mcp" && (
                  <div className="settings-grid">
                    <label>
                      服务地址
                      <input
                        type="url"
                        value={forms[provider].endpoint}
                        onChange={(event) => update(provider, "endpoint", event.target.value)}
                        placeholder="https://…/v1"
                      />
                    </label>
                    <label>
                      模型名称
                      <input
                        value={forms[provider].model}
                        onChange={(event) => update(provider, "model", event.target.value)}
                        placeholder={provider === "ocr" ? "vision-model" : "legal-model"}
                      />
                    </label>
                  </div>
                )}
                <label>
                  {provider === "mcp" ? "MCP Token" : "API Key"}
                  <input
                    type="password"
                    value={forms[provider].secret}
                    onChange={(event) => update(provider, "secret", event.target.value)}
                    placeholder={status?.configured ? "留空则保持现有密钥" : "输入后将加密保存"}
                    autoComplete="new-password"
                  />
                </label>
              </section>
            );
          })}

          <div className="settings-actions">
            <span><ShieldCheck size={15} />数据库只保存密文，API 不回显密钥</span>
            <button disabled={saving || !accessToken.trim()}>
              {saving ? "正在保存…" : "保存设置"}
            </button>
          </div>
        </form>
      </aside>
    </div>
  );
}

function PanelTitle({ icon: Icon, title, meta }) {
  return (
    <div className="panel-title">
      <div><Icon size={18} /><h2>{title}</h2></div>
      <span>{meta}</span>
    </div>
  );
}

function Conversation({ events, pendingQuestions, pendingConfirmation, missingInformation }) {
  const visible = events.filter((event) => (
    event.visibility === "user"
    && (eventLabels[event.event_type] || event.event_type === "message.received")
  ));
  return (
    <div className="conversation-body">
      {visible.length === 0 && (
        <div className="conversation-intro">
          <Scale size={22} />
          <p>请直接描述发生了什么、你希望解决什么。系统会追问缺失事实，不会替你虚构案情或补写外部工具失败后的结论。</p>
        </div>
      )}
      {visible.map((event) => (
        event.event_type === "message.received" ? (
          <div key={event.event_id} className="user-message">
            <small>你的陈述 · #{event.sequence}</small>
            <p>{event.payload.text}</p>
          </div>
        ) : (
          <div
            key={event.event_id}
            className={`timeline-event ${[
              "operation.failed",
              "tool.call_failed",
              "patch.rejected",
              "command.rejected",
            ].includes(event.event_type) ? "failed" : ""}`}
          >
            <span />
            <div>
              <b>{eventLabels[event.event_type] || event.event_type}</b>
              <small>{eventDetail(event)}</small>
              <em>#{event.sequence}</em>
            </div>
          </div>
        )
      ))}
      {pendingConfirmation && (
        <div className="question-card confirmation">
          <b>等待事实确认</b>
          <p>{pendingConfirmation.prompt}</p>
        </div>
      )}
      {pendingQuestions.map((item) => (
        <div className="question-card" key={item.question_id}>
          <b>还需要你确认</b>
          <p>{item.text}</p>
        </div>
      ))}
      {missingInformation.map((item) => (
        <div className="question-card missing" key={item.information_id}>
          <b>{item.blocking ? "阻塞信息" : "补充信息"}</b>
          <p>{item.description}</p>
        </div>
      ))}
    </div>
  );
}

function eventDetail(event) {
  if (event.event_type === "clarification.requested") {
    return event.payload.question
      || event.payload.questions?.map((item) => item.text).join("；")
      || "请查看待补信息";
  }
  return event.payload.summary
    || event.payload.message
    || event.payload.code
    || "已写入案件审计记录";
}

function Composer({ disabled, disabledHint, onSend }) {
  const [text, setText] = useState("");
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        const value = text.trim();
        if (!value || disabled) return;
        onSend(value);
        setText("");
      }}
    >
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        disabled={disabled}
        placeholder={disabledHint || "例如：公司在 7 月 20 日以绩效不合格为由口头辞退我，没有书面通知……"}
      />
      <div>
        <small>{disabledHint || "事实将先进入待确认状态"}</small>
        <button disabled={disabled || !text.trim()}><Send size={17} /> 发送</button>
      </div>
    </form>
  );
}

function Facts({ facts, conflicts, disabled, onConfirm }) {
  return (
    <div className="stack-list">
      {facts.length === 0 && <Muted text="尚未形成候选事实" />}
      {facts.map((fact) => (
        <div className="fact-row" key={fact.fact_id}>
          <div>
            <b>{humanizeFact(fact.fact_id)}</b>
            <span>{String(fact.value)}</span>
            <small className={`status ${fact.status}`}>
              {fact.status === "confirmed"
                ? "已确认"
                : fact.status === "disputed"
                  ? "存在冲突"
                  : "待确认"}
            </small>
          </div>
          {fact.status !== "confirmed" && fact.status !== "disputed" && (
            <button disabled={disabled} onClick={() => onConfirm(fact)}>
              <Check size={14} /> 确认
            </button>
          )}
        </div>
      ))}
      {conflicts.map((conflict) => (
        <div className="conflict-card" key={conflict.conflict_id}>
          <b>事实冲突：{humanizeFact(conflict.fact_id)}</b>
          <p>已有：{String(conflict.existing.value)}<br />新材料：{String(conflict.incoming.value)}</p>
          <div>
            <button disabled={disabled} onClick={() => onConfirm(conflict.existing, conflict.conflict_id)}>
              采用已有值
            </button>
            <button disabled={disabled} onClick={() => onConfirm(conflict.incoming, conflict.conflict_id)}>
              采用新值
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function Evidence({ items, disabled, onUpload }) {
  const statusText = {
    registered: "已登记，等待解析",
    parsing: "正在解析",
    parsed: "已解析",
    failed: "解析失败",
  };
  return (
    <div>
      <label className={`upload-box ${disabled ? "disabled" : ""}`}>
        <Upload size={20} />
        <span>
          <b>{disabled ? "建立案件后可上传证据材料" : "上传劳动合同、工资材料或解除通知"}</b>
          <small>支持 TXT、CSV、JSON、PDF、DOCX、PNG、JPEG，单文件不超过 20 MB</small>
        </span>
        <input
          type="file"
          accept=".txt,.csv,.json,.pdf,.docx,.png,.jpg,.jpeg"
          disabled={disabled}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
            event.target.value = "";
          }}
        />
      </label>
      <div className="evidence-list">
        {items.length === 0 && <Muted text="证据解析状态和事实链接会显示在这里" />}
        {items.map((item) => (
          <div key={item.evidence_id} className={`evidence-item ${item.status}`}>
            <FileText size={17} />
            <span>
              <b>{item.display_name}</b>
              <small>
                {statusText[item.status] || item.status}
                {item.status === "parsed" ? ` · ${item.linked_fact_ids.length} 个事实链接` : ""}
              </small>
            </span>
            <em>{item.sha256.slice(0, 8)}</em>
          </div>
        ))}
      </div>
    </div>
  );
}

function Issues({ issues, authorities }) {
  return (
    <div className="issues-layout">
      <div>
        {issues.length === 0
          ? <Muted text="生成法律分析后，争议焦点会显示在这里" />
          : issues.map((item) => (
            <article className="issue-card" key={item.issue_id}>
              <b>{item.title}</b>
              <p>{item.conclusion}</p>
              <small>
                {item.fact_ids.length} 项事实 · {item.evidence_ids.length} 份证据 · {item.authority_ids.length} 条法源
              </small>
            </article>
          ))}
      </div>
      <div className="authority-list">
        {authorities.length === 0 && <Muted text="尚无法源记录" />}
        {authorities.map((item) => (
          <a
            key={item.authority_id}
            href={item.source_url || undefined}
            target="_blank"
            rel="noreferrer"
          >
            <BookOpen size={15} />
            <span>
              <b>{item.title || item.source_id}</b>
              <small>{item.tool_name} · {item.parsed_status}</small>
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}

function Actions({ activeCase, busy, onCommand, onOpen }) {
  const wageFact = activeCase.candidate_facts.find(
    (item) => item.fact_id === "employment.monthly_wage",
  );
  const [artifact, setArtifact] = useState(null);
  const [revisionIndex, setRevisionIndex] = useState(-1);
  const revision = artifact?.revisions.at(revisionIndex);
  return (
    <div className="actions-wrap">
      <div className="action-buttons">
        <button
          disabled={busy || wageFact?.status !== "confirmed"}
          onClick={() => onCommand({
            command_type: "calculate_rule",
            calc_type: "wage_base",
            inputs: { monthly_wage: wageFact.value },
            fact_ids: ["employment.monthly_wage"],
          })}
        >
          <Calculator size={16} /> 计算工资基数
        </button>
        <button
          disabled={busy || activeCase.authorities.length === 0}
          onClick={() => onCommand({ command_type: "request_analysis" })}
        >
          <Scale size={16} /> 生成法律分析
        </button>
        <button
          disabled={busy || activeCase.issues.length === 0}
          onClick={() => onCommand({
            command_type: "request_document",
            document_type: "labour_arbitration_application",
          })}
        >
          <FileText size={16} /> 生成仲裁申请书
        </button>
      </div>

      {activeCase.rule_results.length > 0 && (
        <div className="rule-strip">
          {activeCase.rule_results.map((item) => (
            <div key={item.result_id}>
              <span>{item.rule}</span>
              <b>{formatRuleResult(item.result)} {item.unit}</b>
              <small>基于 v{item.input_case_version} · {item.fact_ids.length} 项事实</small>
            </div>
          ))}
        </div>
      )}

      <div className="artifact-list">
        {activeCase.artifacts.length === 0 && <Muted text="尚未生成分析或文书" />}
        {activeCase.artifacts.map((item) => (
          <button
            key={item.artifact_id}
            onClick={async () => {
              setArtifact(await onOpen(item.artifact_id));
              setRevisionIndex(-1);
            }}
          >
            <span className={item.stale ? "artifact-icon stale" : "artifact-icon"}>
              <FileText size={17} />
            </span>
            <span>
              <b>{item.title}</b>
              <small>{item.stale ? "案件已变化 · 建议重新生成" : `共 ${item.revision_count} 个版本`}</small>
            </span>
            <ChevronRight size={15} />
          </button>
        ))}
      </div>

      {artifact && (
        <div className="document-view">
          <div className="document-head">
            <span><b>{artifact.title}</b><small>{artifact.artifact_type}</small></span>
            <button onClick={() => setArtifact(null)} aria-label="关闭文书"><X size={15} /></button>
          </div>
          <div className="revision-tabs">
            {artifact.revisions.map((item, index) => (
              <button
                key={item.revision}
                className={item === revision ? "active" : ""}
                onClick={() => setRevisionIndex(index)}
              >
                版本 {item.revision}
              </button>
            ))}
          </div>
          <pre>{revision?.content}</pre>
          <div className="document-trace">
            引用 {revision?.fact_ids.length || 0} 项事实 · {revision?.evidence_ids.length || 0} 份证据 · {revision?.authority_ids.length || 0} 条法源 · {revision?.rule_result_ids.length || 0} 个规则结果
          </div>
        </div>
      )}
    </div>
  );
}

function BrowseActions({ connected }) {
  return (
    <div className="actions-wrap browse-actions">
      <div className="action-buttons">
        <button disabled><Calculator size={16} /> 计算工资基数</button>
        <button disabled><Scale size={16} /> 生成法律分析</button>
        <button disabled><FileText size={16} /> 生成仲裁申请书</button>
      </div>
      <FeatureEmpty
        title="形成可追溯的规则结果与文书版本"
        text={connected
          ? "新建案件并确认事实后，可在这里计算、分析和生成文书。"
          : "连接并建立案件后，可在这里计算、分析和生成文书。"}
      />
    </div>
  );
}

function FeatureEmpty({ title, text }) {
  return (
    <div className="feature-empty">
      <FileCheck2 size={18} />
      <div><b>{title}</b><p>{text}</p></div>
    </div>
  );
}

function formatRuleResult(result) {
  const value = result.amount ?? result.wage_base ?? result.value;
  return value === undefined ? JSON.stringify(result) : String(value);
}

function settingsErrorMessage(error) {
  if (error.code === "authentication_required") return "访问令牌无效，请检查后重新保存。";
  if (error.code === "integration_admin_required") return "当前访问令牌没有修改外部服务配置的权限。";
  return error.message || "设置保存失败";
}

function streamLabel(state) {
  if (state === "online") return "事件在线";
  if (state === "reconnecting") return "正在重连";
  if (state === "connecting") return "正在连接";
  return "事件离线";
}

function Muted({ text }) {
  return <div className="muted"><FileCheck2 size={17} />{text}</div>;
}

function humanizeFact(id) {
  return ({
    "employment.monthly_wage": "月工资",
    "employment.start_date": "入职日期",
    "termination.date": "解除日期",
    "termination.written_notice": "书面解除通知",
    "parties.employer": "用人单位",
    "parties.employee": "劳动者",
  })[id] || id;
}

export default App;
