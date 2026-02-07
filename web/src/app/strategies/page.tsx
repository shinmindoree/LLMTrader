"use client";

import { useEffect, useRef, useState } from "react";

import {
  deleteStrategyChatSession,
  deleteStrategy,
  getStrategyContent,
  generateStrategyStream,
  intakeStrategy,
  listStrategyChatSessions,
  listStrategies,
  saveStrategy,
  strategyChat,
  upsertStrategyChatSession,
  validateStrategySyntax,
} from "@/lib/api";
import type {
  StrategyChatSessionRecord,
  StrategyInfo,
  StrategyIntakeResponse,
  StrategySyntaxCheckResponse,
} from "@/lib/types";

const MODIFY_KEYWORDS =
  /수정|바꿔|변경|추가해|제거|고쳐|개선|개선안|반영|적용|change|modify|update|add|remove|rewrite|revise|바꿔줘|수정해줘|변경해줘|적용해줘|반영해줘|다시\s*만들|다시\s*생성|다시\s*전략\s*생성|전략\s*재생성|재생성|regenerate/i;

function isModifyIntent(text: string): boolean {
  return MODIFY_KEYWORDS.test(text.trim());
}

function getLastCodeAndSummary(messages: ChatMessage[]): {
  code: string;
  summary: string | null;
} | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "assistant" && m.content && !m.textOnly) {
      return { code: m.content, summary: m.summary ?? null };
    }
  }
  return null;
}

function toApiMessages(messages: ChatMessage[]): { role: string; content: string }[] {
  return messages.map((m) => ({
    role: m.role,
    content:
      m.role === "assistant" && (m.summary != null || m.textOnly)
        ? (m.summary ?? m.content)
        : m.content,
  }));
}

function buildMessagesForGeneration(
  messages: ChatMessage[],
  latestCode: string | null,
): { role: string; content: string }[] | undefined {
  const base = toApiMessages(messages);
  if (!latestCode) {
    return base.length > 1 ? base : undefined;
  }
  const last = base.pop();
  if (!last) {
    return undefined;
  }
  const out = [
    ...base,
    {
      role: "assistant",
      content:
        "아래는 직전까지 사용 중인 전략 코드입니다. 사용자의 최신 요청이 수정/개선 지시라면 이 코드를 기반으로 재생성하세요.\n\n"
        + latestCode,
    },
    last,
  ];
  return out;
}

type ClarificationField = "symbol" | "timeframe" | "entry_logic" | "exit_logic" | "risk";

const CLARIFICATION_FIELD_ORDER: ClarificationField[] = [
  "symbol",
  "timeframe",
  "entry_logic",
  "exit_logic",
  "risk",
];

const CLARIFICATION_TEMPLATE_LABELS: Record<ClarificationField, string> = {
  symbol: "Symbol (e.g. BTCUSDT)",
  timeframe: "Timeframe (e.g. 1m, 15m, 1h, 4h)",
  entry_logic: "Entry rules (one line)",
  exit_logic: "Exit rules (one line)",
  risk: "Risk settings (e.g. fixed size / account ratio / stop-loss rule)",
};

function normalizeQuestion(text: string): string {
  return text.toLowerCase().replace(/[^0-9a-z가-힣]/g, "");
}

function classifyQuestion(question: string): ClarificationField | null {
  const normalized = normalizeQuestion(question);
  if (!normalized) return null;
  if (
    normalized.includes("symbol") ||
    normalized.includes("ticker") ||
    normalized.includes("pair") ||
    normalized.includes("심볼") ||
    normalized.includes("종목") ||
    normalized.includes("티커") ||
    normalized.includes("거래쌍")
  ) {
    return "symbol";
  }
  if (
    normalized.includes("timeframe") ||
    normalized.includes("interval") ||
    normalized.includes("candle") ||
    normalized.includes("타임프레임") ||
    normalized.includes("캔들") ||
    normalized.includes("봉")
  ) {
    return "timeframe";
  }
  if (
    normalized.includes("entry") ||
    normalized.includes("enter") ||
    normalized.includes("진입") ||
    normalized.includes("매수조건") ||
    normalized.includes("롱조건")
  ) {
    return "entry_logic";
  }
  if (
    normalized.includes("risk") ||
    normalized.includes("position") ||
    normalized.includes("size") ||
    normalized.includes("leverage") ||
    normalized.includes("리스크") ||
    normalized.includes("위험관리") ||
    normalized.includes("비중") ||
    normalized.includes("수량")
  ) {
    return "risk";
  }
  if (
    normalized.includes("exit") ||
    normalized.includes("close") ||
    normalized.includes("takeprofit") ||
    normalized.includes("stoploss") ||
    normalized.includes("청산") ||
    normalized.includes("익절") ||
    normalized.includes("손절")
  ) {
    return "exit_logic";
  }
  return null;
}

function dedupeClarificationQuestions(questions: string[]): string[] {
  const deduped: string[] = [];
  const seenText = new Set<string>();
  const seenCategory = new Set<ClarificationField>();
  for (const raw of questions) {
    const q = raw.trim();
    if (!q) continue;
    const key = normalizeQuestion(q);
    if (!key || seenText.has(key)) continue;
    const category = classifyQuestion(q);
    if (category && seenCategory.has(category)) continue;
    seenText.add(key);
    if (category) seenCategory.add(category);
    deduped.push(q);
  }
  return deduped;
}

function buildClarificationTemplate(
  intake: StrategyIntakeResponse,
  questions: string[],
): ClarificationField[] {
  const missing = new Set(intake.missing_fields.map((field) => field.trim()));
  const fields: ClarificationField[] = [];
  const addField = (field: ClarificationField) => {
    if (!fields.includes(field)) fields.push(field);
  };

  for (const field of CLARIFICATION_FIELD_ORDER) {
    if (missing.has(field)) {
      addField(field);
    }
  }
  for (const question of questions) {
    const field = classifyQuestion(question);
    if (field) {
      addField(field);
    }
  }
  return fields;
}

function formatIntakeGuidance(intake: StrategyIntakeResponse): string {
  const lines: string[] = [intake.user_message];
  const clarificationQuestions = dedupeClarificationQuestions(intake.clarification_questions);
  if (clarificationQuestions.length > 0) {
    lines.push("", "Additional details needed:");
    clarificationQuestions.forEach((q, idx) => {
      lines.push(`${idx + 1}. ${q}`);
    });
  }
  if (intake.status === "NEEDS_CLARIFICATION") {
    const templateFields = buildClarificationTemplate(intake, clarificationQuestions);
    if (templateFields.length > 0) {
      lines.push("", "Please reply in this format (copy and fill in values):");
      templateFields.forEach((field) => {
        lines.push(`${CLARIFICATION_TEMPLATE_LABELS[field]}:`);
      });
    }
  }
  if (intake.unsupported_requirements.length > 0) {
    lines.push("", "Currently unsupported:");
    intake.unsupported_requirements.forEach((item) => {
      lines.push(`- ${item}`);
    });
  }
  if (intake.development_requirements.length > 0) {
    lines.push("", "Requires additional development:");
    intake.development_requirements.forEach((item, idx) => {
      lines.push(`${idx + 1}. ${item}`);
    });
  }
  if (intake.assumptions.length > 0) {
    lines.push("", "Current system assumptions:");
    intake.assumptions.forEach((item) => {
      lines.push(`- ${item}`);
    });
  }
  return lines.join("\n");
}

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

type DiffLine = {
  type: "context" | "add" | "remove";
  leftLineNo: number | null;
  rightLineNo: number | null;
  text: string;
};

function buildCodeDiffLines(beforeCode: string, afterCode: string): DiffLine[] {
  const beforeLines = beforeCode.split("\n");
  const afterLines = afterCode.split("\n");
  const n = beforeLines.length;
  const m = afterLines.length;

  if (n === 0 && m === 0) return [];

  // Guardrail for very large files to avoid expensive LCS matrix.
  if (n * m > 240_000) {
    const out: DiffLine[] = [];
    const max = Math.max(n, m);
    for (let i = 0; i < max; i++) {
      const before = beforeLines[i];
      const after = afterLines[i];
      if (before === after) {
        out.push({
          type: "context",
          leftLineNo: before !== undefined ? i + 1 : null,
          rightLineNo: after !== undefined ? i + 1 : null,
          text: before ?? after ?? "",
        });
        continue;
      }
      if (before !== undefined) {
        out.push({
          type: "remove",
          leftLineNo: i + 1,
          rightLineNo: null,
          text: before,
        });
      }
      if (after !== undefined) {
        out.push({
          type: "add",
          leftLineNo: null,
          rightLineNo: i + 1,
          text: after,
        });
      }
    }
    return out;
  }

  const lcs = Array.from({ length: n + 1 }, () => Array<number>(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (beforeLines[i - 1] === afterLines[j - 1]) {
        lcs[i][j] = lcs[i - 1][j - 1] + 1;
      } else {
        lcs[i][j] = Math.max(lcs[i - 1][j], lcs[i][j - 1]);
      }
    }
  }

  type Op = { type: "context" | "add" | "remove"; text: string };
  const ops: Op[] = [];
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (beforeLines[i - 1] === afterLines[j - 1]) {
      ops.push({ type: "context", text: beforeLines[i - 1] });
      i -= 1;
      j -= 1;
      continue;
    }
    if (lcs[i - 1][j] >= lcs[i][j - 1]) {
      ops.push({ type: "remove", text: beforeLines[i - 1] });
      i -= 1;
    } else {
      ops.push({ type: "add", text: afterLines[j - 1] });
      j -= 1;
    }
  }
  while (i > 0) {
    ops.push({ type: "remove", text: beforeLines[i - 1] });
    i -= 1;
  }
  while (j > 0) {
    ops.push({ type: "add", text: afterLines[j - 1] });
    j -= 1;
  }
  ops.reverse();

  const out: DiffLine[] = [];
  let leftLine = 1;
  let rightLine = 1;
  for (const op of ops) {
    if (op.type === "context") {
      out.push({
        type: "context",
        leftLineNo: leftLine,
        rightLineNo: rightLine,
        text: op.text,
      });
      leftLine += 1;
      rightLine += 1;
      continue;
    }
    if (op.type === "remove") {
      out.push({
        type: "remove",
        leftLineNo: leftLine,
        rightLineNo: null,
        text: op.text,
      });
      leftLine += 1;
      continue;
    }
    out.push({
      type: "add",
      leftLineNo: null,
      rightLineNo: rightLine,
      text: op.text,
    });
    rightLine += 1;
  }
  return out;
}

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const CHAT_SESSIONS_KEY = "llmtrader.strategy_chat_sessions.v1";

function persistExecutionDefaults(spec: StrategyIntakeResponse["normalized_spec"] | null | undefined): void {
  if (!spec || typeof window === "undefined") {
    return;
  }
  const symbol = typeof spec.symbol === "string" ? spec.symbol.trim().toUpperCase() : "";
  const interval = typeof spec.timeframe === "string" ? spec.timeframe.trim() : "";
  if (!symbol && !interval) {
    return;
  }
  try {
    const prevRaw = window.localStorage.getItem(EXECUTION_DEFAULTS_KEY);
    const prev = prevRaw ? (JSON.parse(prevRaw) as Record<string, unknown>) : {};
    const next: Record<string, unknown> = {
      ...prev,
      updated_at: new Date().toISOString(),
    };
    if (symbol) next.symbol = symbol;
    if (interval) next.interval = interval;
    window.localStorage.setItem(EXECUTION_DEFAULTS_KEY, JSON.stringify(next));
  } catch {
    // ignore storage errors
  }
}

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  model?: string | null;
  path?: string | null;
  summary?: string | null;
  backtest_ok?: boolean;
  repaired?: boolean;
  repair_attempts?: number;
  textOnly?: boolean;
  status?: "thinking" | "typing" | "streaming" | null;
  statusText?: string | null;
};

type ChatSessionRecord = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
  workspaceCode: string;
  workspaceSummary: string | null;
  workspaceSourceMessageId: string | null;
  initialGeneratedCode: string | null;
};

type StoredChatSessionsPayload = {
  version: 1;
  sessions: ChatSessionRecord[];
  activeSessionId: string | null;
};

const createId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;

const isPresent = <T,>(value: T | null | undefined): value is T =>
  value !== null && value !== undefined;

function toOptionalString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value;
}

function sanitizeChatMessage(value: unknown): ChatMessage | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const role = raw.role;
  if (role !== "user" && role !== "assistant") return null;
  const statusRaw = raw.status;
  const status =
    statusRaw === "thinking" || statusRaw === "typing" || statusRaw === "streaming"
      ? statusRaw
      : null;
  const repairedRaw = raw.repaired;
  const backtestOkRaw = raw.backtest_ok;
  const repairAttemptsRaw = raw.repair_attempts;
  const textOnlyRaw = raw.textOnly;
  return {
    id: typeof raw.id === "string" && raw.id.trim() ? raw.id : createId(),
    role,
    content: typeof raw.content === "string" ? raw.content : "",
    model: toOptionalString(raw.model),
    path: toOptionalString(raw.path),
    summary: toOptionalString(raw.summary),
    backtest_ok: typeof backtestOkRaw === "boolean" ? backtestOkRaw : false,
    repaired: typeof repairedRaw === "boolean" ? repairedRaw : false,
    repair_attempts: typeof repairAttemptsRaw === "number" ? repairAttemptsRaw : 0,
    textOnly: typeof textOnlyRaw === "boolean" ? textOnlyRaw : false,
    status,
    statusText: toOptionalString(raw.statusText),
  };
}

function sanitizeChatSession(value: unknown): ChatSessionRecord | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const id = typeof raw.id === "string" && raw.id.trim() ? raw.id : null;
  if (!id) return null;
  const now = new Date().toISOString();
  const createdAt = typeof raw.createdAt === "string" ? raw.createdAt : now;
  const updatedAt = typeof raw.updatedAt === "string" ? raw.updatedAt : createdAt;
  const rawMessages = Array.isArray(raw.messages) ? raw.messages : [];
  const messages = rawMessages.map((item) => sanitizeChatMessage(item)).filter(isPresent);
  const fallbackTitle = "New chat";
  const title =
    typeof raw.title === "string" && raw.title.trim()
      ? raw.title
      : fallbackTitle;
  return {
    id,
    title,
    createdAt,
    updatedAt,
    messages,
    workspaceCode: typeof raw.workspaceCode === "string" ? raw.workspaceCode : "",
    workspaceSummary: toOptionalString(raw.workspaceSummary),
    workspaceSourceMessageId: toOptionalString(raw.workspaceSourceMessageId),
    initialGeneratedCode: toOptionalString(raw.initialGeneratedCode),
  };
}

function sortSessionsByUpdated(sessions: ChatSessionRecord[]): ChatSessionRecord[] {
  return [...sessions].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

function deriveSessionTitle(messages: ChatMessage[]): string {
  const firstUserMessage = messages.find((message) => message.role === "user" && message.content.trim());
  if (!firstUserMessage) return "New chat";
  const normalized = firstUserMessage.content.replace(/\s+/g, " ").trim();
  if (normalized.length <= 36) return normalized;
  return `${normalized.slice(0, 36)}...`;
}

function createEmptySession(): ChatSessionRecord {
  const now = new Date().toISOString();
  return {
    id: createId(),
    title: "New chat",
    createdAt: now,
    updatedAt: now,
    messages: [],
    workspaceCode: "",
    workspaceSummary: null,
    workspaceSourceMessageId: null,
    initialGeneratedCode: null,
  };
}

function formatSessionTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  return parsed.toLocaleString();
}

function fromRemoteSessionRecord(remote: StrategyChatSessionRecord): ChatSessionRecord | null {
  const data = remote.data ?? {};
  return sanitizeChatSession({
    id: remote.session_id,
    title: remote.title,
    createdAt: remote.created_at,
    updatedAt: remote.updated_at,
    messages: (data as { messages?: unknown }).messages,
    workspaceCode: (data as { workspaceCode?: unknown }).workspaceCode,
    workspaceSummary: (data as { workspaceSummary?: unknown }).workspaceSummary,
    workspaceSourceMessageId: (data as { workspaceSourceMessageId?: unknown }).workspaceSourceMessageId,
    initialGeneratedCode: (data as { initialGeneratedCode?: unknown }).initialGeneratedCode,
  });
}

function toRemoteSessionData(session: ChatSessionRecord): Record<string, unknown> {
  return {
    messages: session.messages,
    workspaceCode: session.workspaceCode,
    workspaceSummary: session.workspaceSummary,
    workspaceSourceMessageId: session.workspaceSourceMessageId,
    initialGeneratedCode: session.initialGeneratedCode,
  };
}

type TabId = "chat" | "list";
const SUMMARY_EXPAND_PROMPT =
  "방금 전략 요약을 이어서 더 자세히 설명해줘. 전략 개요 → 진입 흐름 → 청산 흐름 → 리스크 관리 → 실전 주의사항 순서로 써줘. 코드는 변경하지 마.";
const LOADED_STRATEGY_SUMMARY_PROMPT =
  "Briefly explain this strategy in plain English in 5 bullets: overview, entry logic, exit logic, risk management, and practical cautions. Keep it concise.";

export default function StrategiesPage() {
  const [activeTab, setActiveTab] = useState<TabId>("chat");
  const [items, setItems] = useState<StrategyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [deletingPath, setDeletingPath] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [saveModal, setSaveModal] = useState<{
    messageId: string;
    code: string;
    name: string;
  } | null>(null);
  const [loadStrategyPath, setLoadStrategyPath] = useState("");
  const [isLoadingStrategy, setIsLoadingStrategy] = useState(false);
  const [loadStrategyError, setLoadStrategyError] = useState<string | null>(null);
  const [workspaceCode, setWorkspaceCode] = useState("");
  const [workspaceSourceMessageId, setWorkspaceSourceMessageId] = useState<string | null>(null);
  const [initialGeneratedCode, setInitialGeneratedCode] = useState<string | null>(null);
  const [workspaceSummary, setWorkspaceSummary] = useState<string | null>(null);
  const [workspaceDirty, setWorkspaceDirty] = useState(false);
  const [workspaceChecking, setWorkspaceChecking] = useState(false);
  const [workspaceSyntax, setWorkspaceSyntax] = useState<StrategySyntaxCheckResponse | null>(null);
  const [workspaceSyntaxError, setWorkspaceSyntaxError] = useState<string | null>(null);
  const [workspaceOpen, setWorkspaceOpen] = useState(true);
  const [workspaceWidth, setWorkspaceWidth] = useState(440);
  const [isResizingWorkspace, setIsResizingWorkspace] = useState(false);
  const [isComposingPrompt, setIsComposingPrompt] = useState(false);
  const [chatSessions, setChatSessions] = useState<ChatSessionRecord[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [sessionsReady, setSessionsReady] = useState(false);
  const [sessionSyncError, setSessionSyncError] = useState<string | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const workspaceGutterRef = useRef<HTMLDivElement | null>(null);
  const workspaceTextAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const workspaceResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const skipSessionSyncRef = useRef(false);
  const chatSessionsRef = useRef<ChatSessionRecord[]>([]);

  useEffect(() => {
    listStrategies()
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    let cancelled = false;

    const applyFromLocalStorage = () => {
      try {
        const raw = window.localStorage.getItem(CHAT_SESSIONS_KEY);
        let loadedSessions: ChatSessionRecord[] = [];
        let loadedActiveSessionId: string | null = null;
        if (raw) {
          const parsed = JSON.parse(raw) as unknown;
          if (Array.isArray(parsed)) {
            loadedSessions = parsed.map((item) => sanitizeChatSession(item)).filter(isPresent);
          } else if (parsed && typeof parsed === "object") {
            const payload = parsed as Record<string, unknown>;
            if (Array.isArray(payload.sessions)) {
              loadedSessions = payload.sessions
                .map((item) => sanitizeChatSession(item))
                .filter(isPresent);
            }
            loadedActiveSessionId =
              typeof payload.activeSessionId === "string" ? payload.activeSessionId : null;
          }
        }

        if (loadedSessions.length === 0) {
          const initialSession = createEmptySession();
          setChatSessions([initialSession]);
          setActiveSessionId(initialSession.id);
          return;
        }
        const sorted = sortSessionsByUpdated(loadedSessions);
        const resolvedActiveId =
          loadedActiveSessionId && sorted.some((session) => session.id === loadedActiveSessionId)
            ? loadedActiveSessionId
            : sorted[0].id;
        setChatSessions(sorted);
        setActiveSessionId(resolvedActiveId);
      } catch {
        const fallbackSession = createEmptySession();
        setChatSessions([fallbackSession]);
        setActiveSessionId(fallbackSession.id);
      }
    };

    const loadSessions = async () => {
      try {
        const remote = await listStrategyChatSessions();
        if (cancelled) return;
        const remoteSessions = remote
          .map((item) => fromRemoteSessionRecord(item))
          .filter(isPresent);
        if (remoteSessions.length > 0) {
          const sorted = sortSessionsByUpdated(remoteSessions);
          setChatSessions(sorted);
          setActiveSessionId(sorted[0].id);
          setSessionSyncError(null);
          return;
        }
      } catch (e) {
        if (!cancelled) {
          setSessionSyncError(`Remote session load failed: ${String(e)}`);
        }
      }
      if (!cancelled) {
        applyFromLocalStorage();
      }
    };

    void loadSessions().finally(() => {
      if (!cancelled) {
        setSessionsReady(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (items.length === 0) {
      setLoadStrategyPath("");
      return;
    }
    if (!loadStrategyPath || !items.some((item) => item.path === loadStrategyPath)) {
      setLoadStrategyPath(items[0].path);
    }
  }, [items, loadStrategyPath]);

  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [chatMessages]);

  useEffect(() => {
    chatSessionsRef.current = chatSessions;
  }, [chatSessions]);

  useEffect(() => {
    if (!sessionsReady || !activeSessionId) {
      return;
    }
    const activeSession = chatSessionsRef.current.find((session) => session.id === activeSessionId);
    if (!activeSession) {
      return;
    }
    skipSessionSyncRef.current = true;
    setChatMessages(activeSession.messages);
    setChatError(null);
    setPrompt("");
    setWorkspaceCode(activeSession.workspaceCode);
    setWorkspaceSourceMessageId(activeSession.workspaceSourceMessageId);
    setInitialGeneratedCode(activeSession.initialGeneratedCode);
    setWorkspaceSummary(activeSession.workspaceSummary);
    setWorkspaceDirty(false);
    setWorkspaceSyntax(null);
    setWorkspaceSyntaxError(null);
  }, [activeSessionId, sessionsReady]);

  useEffect(() => {
    if (!sessionsReady || !activeSessionId) {
      return;
    }
    if (skipSessionSyncRef.current) {
      skipSessionSyncRef.current = false;
      return;
    }
    setChatSessions((prev) => {
      const idx = prev.findIndex((session) => session.id === activeSessionId);
      if (idx < 0) return prev;
      const current = prev[idx];
      const next: ChatSessionRecord = {
        ...current,
        title: deriveSessionTitle(chatMessages),
        updatedAt: new Date().toISOString(),
        messages: chatMessages,
        workspaceCode,
        workspaceSummary,
        workspaceSourceMessageId,
        initialGeneratedCode,
      };
      const updatedSessions = [...prev];
      updatedSessions[idx] = next;
      return sortSessionsByUpdated(updatedSessions);
    });
  }, [
    activeSessionId,
    chatMessages,
    initialGeneratedCode,
    sessionsReady,
    workspaceCode,
    workspaceSourceMessageId,
    workspaceSummary,
  ]);

  useEffect(() => {
    if (!sessionsReady || typeof window === "undefined") {
      return;
    }
    try {
      const payload: StoredChatSessionsPayload = {
        version: 1,
        sessions: chatSessions,
        activeSessionId,
      };
      window.localStorage.setItem(CHAT_SESSIONS_KEY, JSON.stringify(payload));
    } catch {
      // ignore storage errors
    }
  }, [activeSessionId, chatSessions, sessionsReady]);

  useEffect(() => {
    if (!sessionsReady || !activeSessionId || typeof window === "undefined") {
      return;
    }
    const activeSession = chatSessions.find((session) => session.id === activeSessionId);
    if (!activeSession) {
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          await upsertStrategyChatSession(activeSession.id, {
            title: activeSession.title,
            data: toRemoteSessionData(activeSession),
          });
          if (!cancelled) {
            setSessionSyncError(null);
          }
        } catch (e) {
          if (!cancelled) {
            setSessionSyncError(`Remote sync failed: ${String(e)}`);
          }
        }
      })();
    }, 650);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeSessionId, chatSessions, sessionsReady]);

  useEffect(() => {
    const code = workspaceCode;
    if (!code.trim()) {
      setWorkspaceChecking(false);
      setWorkspaceSyntax(null);
      setWorkspaceSyntaxError(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setWorkspaceChecking(true);
      setWorkspaceSyntaxError(null);
      validateStrategySyntax(code)
        .then((res) => {
          if (cancelled) return;
          setWorkspaceSyntax(res);
        })
        .catch((e) => {
          if (cancelled) return;
          setWorkspaceSyntax(null);
          setWorkspaceSyntaxError(String(e));
        })
        .finally(() => {
          if (cancelled) return;
          setWorkspaceChecking(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [workspaceCode]);

  useEffect(() => {
    if (!isResizingWorkspace) {
      return;
    }
    const onMouseMove = (event: MouseEvent) => {
      const state = workspaceResizeRef.current;
      if (!state) return;
      const delta = state.startX - event.clientX;
      const maxWidth = Math.max(360, Math.min(900, window.innerWidth - 460));
      const nextWidth = Math.max(320, Math.min(maxWidth, state.startWidth + delta));
      setWorkspaceWidth(nextWidth);
    };
    const onMouseUp = () => {
      workspaceResizeRef.current = null;
      setIsResizingWorkspace(false);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [isResizingWorkspace]);

  const animateAssistantTyping = async (assistantId: string, fullText: string): Promise<void> => {
    if (!fullText) {
      setChatMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: "", status: null, statusText: null }
            : m,
        ),
      );
      return;
    }
    const chunkSize =
      fullText.length > 2000 ? 24 : fullText.length > 1200 ? 16 : fullText.length > 600 ? 8 : 4;
    let cursor = 0;
    while (cursor < fullText.length) {
      cursor = Math.min(fullText.length, cursor + chunkSize);
      const partial = fullText.slice(0, cursor);
      setChatMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                content: partial,
                textOnly: true,
                status: cursor < fullText.length ? "typing" : null,
                statusText: cursor < fullText.length ? "답변 작성 중..." : null,
              }
            : m,
        ),
      );
      if (cursor < fullText.length) {
        await new Promise((resolve) => window.setTimeout(resolve, 16));
      }
    }
  };

  const submitPrompt = async (trimmed: string, options?: { forceChat?: boolean }) => {
    if (!trimmed || isSending) {
      return;
    }

    setChatError(null);
    setIsSending(true);
    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: trimmed,
    };
    const nextMessages = [...chatMessages, userMessage];
    setChatMessages(nextMessages);

    const lastCodeSummary = getLastCodeAndSummary(nextMessages);
    const workspaceCodeTrimmed = workspaceCode.trim();
    const activeCode = workspaceCodeTrimmed || lastCodeSummary?.code || "";
    const activeSummary = workspaceCodeTrimmed ? workspaceSummary : (lastCodeSummary?.summary ?? null);
    const isFirstTurn = !activeCode;
    const isModify = !options?.forceChat && !isFirstTurn && isModifyIntent(trimmed);

    const doGenerate = (
      messagesToSend?: { role: string; content: string }[],
      intakeSpec?: StrategyIntakeResponse["normalized_spec"] | null,
    ) => {
      return generateStrategyStream(
        trimmed,
        {
          onToken(token) {
            setChatMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.role !== "assistant" || last.id !== assistantId) return prev;
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  content: last.content + token,
                  status: "streaming",
                  statusText: "코드 생성 중...",
                },
              ];
            });
          },
          onDone(payload) {
            if (payload.error) {
              setChatError(payload.error);
              setChatMessages((prev) =>
                prev.filter((m) => m.id !== assistantId),
              );
            } else {
              persistExecutionDefaults(intakeSpec);
              setChatMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last?.role !== "assistant" || last.id !== assistantId) return prev;
                return [
                  ...prev.slice(0, -1),
                  {
                    ...last,
                    content: payload.code ?? last.content,
                    summary: payload.summary ?? null,
                    backtest_ok: payload.backtest_ok ?? false,
                    repaired: payload.repaired ?? false,
                    repair_attempts: payload.repair_attempts ?? 0,
                    status: null,
                    statusText: null,
                  },
                ];
              });
              if (payload.code) {
                setWorkspaceCode(payload.code);
                setWorkspaceSourceMessageId(assistantId);
                setWorkspaceSummary(payload.summary ?? null);
                setWorkspaceDirty(false);
                setInitialGeneratedCode((prev) => prev ?? payload.code ?? null);
              }
              listStrategies()
                .then(setItems)
                .catch((e) => setError(String(e)));
            }
            setIsSending(false);
          },
        },
        undefined,
        messagesToSend,
      );
    };

    const assistantId = createId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      model: null,
      path: null,
      summary: null,
      backtest_ok: false,
      repaired: false,
      repair_attempts: 0,
      textOnly: false,
      status: "thinking",
      statusText: "요청 분석 중...",
    };

    if (isFirstTurn || isModify) {
      setChatMessages((prev) => [...prev, assistantMessage]);
      try {
        const messagesToSend = buildMessagesForGeneration(
          nextMessages,
          isFirstTurn ? null : activeCode,
        );
        const intake = await intakeStrategy(trimmed, messagesToSend);
        if (intake.status !== "READY") {
          const guidance = formatIntakeGuidance(intake);
          setChatMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role !== "assistant" || last.id !== assistantId) return prev;
            return [
              ...prev.slice(0, -1),
              { ...last, content: guidance, textOnly: true, status: null, statusText: null },
            ];
          });
          setIsSending(false);
          return;
        }
        setChatMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, status: "thinking", statusText: "전략 생성 준비 중..." }
              : m,
          ),
        );
        await doGenerate(messagesToSend, intake.normalized_spec);
      } catch (e) {
        setChatError(String(e));
        setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
        setIsSending(false);
      }
      return;
    }

    setChatMessages((prev) => [
      ...prev,
      { ...assistantMessage, textOnly: true, status: "thinking", statusText: "답변 준비 중..." },
    ]);
    try {
      const chatMessagesForApi = toApiMessages(nextMessages);
      const res = await strategyChat(
        activeCode,
        activeSummary,
        chatMessagesForApi,
      );
      await animateAssistantTyping(assistantId, res.content);
    } catch (e) {
      setChatError(String(e));
      setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
    } finally {
      setIsSending(false);
    }
  };

  const handleSubmit = async (event?: React.FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed || isSending) {
      return;
    }
    setPrompt("");
    await submitPrompt(trimmed);
  };

  const handleSummaryExpand = async () => {
    if (isSending) return;
    await submitPrompt(SUMMARY_EXPAND_PROMPT, { forceChat: true });
  };

  const handleWorkspaceChange = (nextCode: string) => {
    setWorkspaceCode(nextCode);
    setWorkspaceDirty(true);
    setWorkspaceSummary(null);
  };

  const handleWorkspaceScroll = () => {
    if (!workspaceTextAreaRef.current || !workspaceGutterRef.current) return;
    workspaceGutterRef.current.scrollTop = workspaceTextAreaRef.current.scrollTop;
  };

  const handleWorkspaceResizeStart = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!workspaceOpen) return;
    workspaceResizeRef.current = {
      startX: event.clientX,
      startWidth: workspaceWidth,
    };
    setIsResizingWorkspace(true);
  };

  const handleWorkspaceToggle = () => {
    setWorkspaceOpen((prev) => !prev);
  };

  const handleSaveWorkspace = () => {
    const code = workspaceCode.trim();
    if (!code) return;
    setSaveModal({
      messageId: workspaceSourceMessageId ?? createId(),
      code,
      name: "",
    });
  };

  const handleSaveClick = (messageId: string, code: string) => {
    setSaveModal({ messageId, code, name: "" });
  };

  const handleSaveConfirm = async () => {
    if (!saveModal || savingId) return;
    setSavingId(saveModal.messageId);
    setChatError(null);
    try {
      await saveStrategy(saveModal.code, saveModal.name.trim() || undefined);
      listStrategies()
        .then(setItems)
        .catch((e) => setError(String(e)));
      setSaveModal(null);
      setActiveTab("list");
    } catch (e) {
      setChatError(String(e));
    } finally {
      setSavingId(null);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter"
      && !event.shiftKey
      && !isComposingPrompt
      && !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      const next = event.currentTarget.value.trim();
      if (!next || isSending) return;
      setPrompt("");
      void submitPrompt(next);
    }
  };

  const handleClear = () => {
    setChatMessages([]);
    setChatError(null);
    setPrompt("");
    setWorkspaceCode("");
    setWorkspaceSourceMessageId(null);
    setInitialGeneratedCode(null);
    setWorkspaceSummary(null);
    setWorkspaceDirty(false);
    setWorkspaceSyntax(null);
    setWorkspaceSyntaxError(null);
  };

  const handleNewChatSession = () => {
    if (isSending) return;
    const nextSession = createEmptySession();
    setChatSessions((prev) => [nextSession, ...prev]);
    setActiveSessionId(nextSession.id);
  };

  const handleSelectSession = (sessionId: string) => {
    if (sessionId === activeSessionId || isSending) return;
    setActiveSessionId(sessionId);
  };

  const handleDeleteSession = (sessionId: string) => {
    if (isSending) return;
    setChatSessions((prev) => {
      const remaining = prev.filter((session) => session.id !== sessionId);
      if (remaining.length === 0) {
        const fallbackSession = createEmptySession();
        setActiveSessionId(fallbackSession.id);
        return [fallbackSession];
      }
      if (activeSessionId === sessionId) {
        setActiveSessionId(remaining[0].id);
      }
      return remaining;
    });
    void deleteStrategyChatSession(sessionId)
      .then(() => setSessionSyncError(null))
      .catch((e) => setSessionSyncError(`Remote delete failed: ${String(e)}`));
  };

  const handleCopy = async (content: string, id: string) => {
    if (!navigator?.clipboard) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setCopiedId(id);
      setTimeout(() => {
        setCopiedId((prev) => (prev === id ? null : prev));
      }, 1500);
    } catch {
      setCopiedId(null);
    }
  };

  const handleDeleteClick = (path: string) => {
    setDeletingPath(path);
    setDeleteError(null);
  };

  const handleDeleteConfirm = async () => {
    if (!deletingPath) return;
    try {
      await deleteStrategy(deletingPath);
      setItems((prev) => prev.filter((s) => s.path !== deletingPath));
      setDeletingPath(null);
      setDeleteError(null);
    } catch (e) {
      setDeleteError(String(e));
    }
  };

  const handleLoadStrategy = async () => {
    if (!loadStrategyPath || isLoadingStrategy || isSending) return;
    setLoadStrategyError(null);
    setChatError(null);
    setIsLoadingStrategy(true);

    try {
      const loaded = await getStrategyContent(loadStrategyPath);
      const code = loaded.code ?? "";
      if (!code.trim()) {
        throw new Error("Loaded strategy is empty.");
      }

      setWorkspaceCode(code);
      setInitialGeneratedCode(code);
      setWorkspaceSourceMessageId(null);
      setWorkspaceSummary(null);
      setWorkspaceDirty(false);
      setWorkspaceOpen(true);
      setWorkspaceSyntax(null);
      setWorkspaceSyntaxError(null);
      setPrompt("");

      const strategyLabel = strategyNameFromPath(loaded.path || loadStrategyPath);
      let summaryText = "Summary is unavailable right now.";
      try {
        const summaryRes = await strategyChat(code, null, [
          { role: "user", content: LOADED_STRATEGY_SUMMARY_PROMPT },
        ]);
        summaryText = summaryRes.content;
        setWorkspaceSummary(summaryRes.content);
      } catch {
        // continue without summary
      }

      setChatMessages([
        {
          id: createId(),
          role: "assistant",
          content: `Loaded strategy: ${strategyLabel}\n\n${summaryText}`,
          textOnly: true,
          status: null,
          statusText: null,
        },
      ]);
    } catch (e) {
      setLoadStrategyError(String(e));
    } finally {
      setIsLoadingStrategy(false);
    }
  };

  const latestAssistantCodeId =
    [...chatMessages]
      .reverse()
      .find((m) => m.role === "assistant" && !m.textOnly && Boolean(m.content))?.id ?? null;
  const workspaceLineCount = Math.max(1, workspaceCode.split("\n").length);
  const syntaxErrorLine = workspaceSyntax?.error?.line ?? null;
  const syntaxErrorColumn =
    typeof workspaceSyntax?.error?.column === "number" ? workspaceSyntax.error.column + 1 : null;
  const workspaceDiffLines =
    initialGeneratedCode && workspaceCode
      ? buildCodeDiffLines(initialGeneratedCode, workspaceCode)
      : [];
  const hasWorkspaceDiff = workspaceDiffLines.some((line) => line.type !== "context");
  const activeSession = activeSessionId
    ? chatSessions.find((session) => session.id === activeSessionId) ?? null
    : null;

  return (
    <main className="flex h-[calc(100vh-3.5rem)] min-h-0 w-full flex-col overflow-hidden px-6 py-6">
      <h1 className="text-xl font-semibold text-[#d1d4dc]">Strategies</h1>
      <div className="mt-4 flex gap-1 border-b border-[#2a2e39]">
        <button
          type="button"
          onClick={() => setActiveTab("chat")}
          className={`rounded-t px-4 py-2 text-sm font-medium transition ${
            activeTab === "chat"
              ? "border border-[#2a2e39] border-b-transparent bg-[#1e222d] text-[#d1d4dc]"
              : "text-[#868993] hover:text-[#d1d4dc]"
          }`}
        >
          Strategy Chat
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("list")}
          className={`rounded-t px-4 py-2 text-sm font-medium transition ${
            activeTab === "list"
              ? "border border-[#2a2e39] border-b-transparent bg-[#1e222d] text-[#d1d4dc]"
              : "text-[#868993] hover:text-[#d1d4dc]"
          }`}
        >
          Strategy List
        </button>
      </div>

      {activeTab === "chat" ? (
      <section className="relative mt-0 flex min-h-0 flex-1 flex-col overflow-hidden rounded-b-lg border border-t-0 border-[#2a2e39] bg-[#1e222d]">
        <div className="flex min-h-0 flex-1">
          <aside className="hidden w-72 shrink-0 border-r border-[#2a2e39] bg-[#171b25] md:flex md:flex-col">
            <div className="border-b border-[#2a2e39] px-3 py-3">
              <button
                type="button"
                className="w-full rounded border border-[#2962ff] px-3 py-2 text-sm text-[#8fa8ff] transition hover:bg-[#2962ff] hover:text-white disabled:opacity-50"
                onClick={handleNewChatSession}
                disabled={isSending || !sessionsReady}
              >
                + New chat
              </button>
            </div>
            <div className="border-b border-[#2a2e39] px-3 py-2 text-xs text-[#868993]">
              {activeSession ? `Current: ${activeSession.title}` : "No active chat"}
            </div>
            {sessionSyncError ? (
              <p className="border-b border-[#ef5350]/30 bg-[#2d1f1f]/40 px-3 py-2 text-[11px] text-[#ef9a9a]">
                {sessionSyncError}
              </p>
            ) : null}
            <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
              {chatSessions.length === 0 ? (
                <p className="px-2 py-3 text-xs text-[#868993]">No chats yet.</p>
              ) : (
                <div className="space-y-1">
                  {chatSessions.map((session) => {
                    const isActive = session.id === activeSessionId;
                    return (
                      <div
                        key={session.id}
                        className={`rounded border p-2 ${
                          isActive
                            ? "border-[#2962ff]/70 bg-[#1a2442]"
                            : "border-[#2a2e39] bg-[#131722]"
                        }`}
                      >
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => handleSelectSession(session.id)}
                          disabled={isSending}
                        >
                          <p className="truncate text-sm text-[#d1d4dc]">{session.title}</p>
                          <p className="mt-1 text-[11px] text-[#868993]">
                            {formatSessionTimestamp(session.updatedAt)}
                          </p>
                          <p className="mt-1 text-[11px] text-[#5f6472]">
                            {session.messages.length} messages
                          </p>
                        </button>
                        <div className="mt-2 flex justify-end">
                          <button
                            type="button"
                            className="rounded border border-[#ef5350]/40 px-2 py-1 text-[11px] text-[#ef9a9a] transition hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50"
                            onClick={() => handleDeleteSession(session.id)}
                            disabled={isSending}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </aside>
          <div className="min-w-0 flex-1 flex flex-col">
            <div className="flex items-center gap-2 border-b border-[#2a2e39] px-4 py-2 md:hidden">
              <select
                className="min-w-0 flex-1 rounded border border-[#2a2e39] bg-[#171b25] px-2 py-1.5 text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                value={activeSessionId ?? ""}
                onChange={(e) => handleSelectSession(e.target.value)}
                disabled={isSending || !sessionsReady}
              >
                {chatSessions.map((session) => (
                  <option key={`mobile-session-${session.id}`} value={session.id}>
                    {session.title}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="shrink-0 rounded border border-[#2962ff] px-2 py-1.5 text-xs text-[#8fa8ff] transition hover:bg-[#2962ff] hover:text-white disabled:opacity-50"
                onClick={handleNewChatSession}
                disabled={isSending || !sessionsReady}
              >
                New
              </button>
            </div>
            {chatMessages.length > 0 ? (
              <>
                <div className="flex flex-shrink-0 justify-end px-4 pt-3">
                  <button
                    type="button"
                    className="rounded border border-[#2a2e39] px-3 py-1 text-xs text-[#868993] transition hover:border-[#2962ff] hover:text-white"
                    onClick={handleClear}
                  >
                    Clear
                  </button>
                </div>
                {chatError ? (
                  <p className="mx-4 mt-2 flex-shrink-0 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
                    {chatError}
                  </p>
                ) : null}
                <div
                  ref={chatScrollRef}
                  className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
                >
                  <div className="mx-auto max-w-3xl space-y-4">
                  {chatMessages.map((message) => {
                    const isLatestAssistantCode = message.id === latestAssistantCodeId;
                    return (
                    <div
                      key={message.id}
                      className={`rounded-lg border px-4 py-3 ${
                        message.role === "user"
                          ? "border-[#2962ff]/40 bg-[#0f1b3a]"
                          : "border-[#2a2e39] bg-[#1e222d]"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2 text-xs uppercase tracking-wide text-[#868993]">
                        <span>{message.role === "user" ? "You" : "LLM"}</span>
                        {message.role === "assistant" && message.content ? (
                          <button
                            className="rounded border border-transparent px-2 py-1 text-xs text-[#9aa0ad] transition hover:border-[#2962ff] hover:text-white"
                            onClick={() => void handleCopy(message.content, message.id)}
                            type="button"
                          >
                            {copiedId === message.id ? "Copied" : "Copy"}
                          </button>
                        ) : null}
                      </div>
                      {message.statusText ? (
                        <div className="mt-2 inline-flex items-center gap-2 rounded border border-[#2a2e39] bg-[#171b25] px-2 py-1 text-xs text-[#8fa8ff]">
                          <span className="h-1.5 w-1.5 rounded-full bg-[#8fa8ff] animate-pulse" />
                          {message.statusText}
                        </div>
                      ) : null}
                      {message.role === "assistant" ? (
                        message.textOnly ? (
                          message.content ? (
                            <p className="mt-2 whitespace-pre-wrap text-sm text-[#d1d4dc]">
                              {message.content}
                            </p>
                          ) : null
                        ) : (
                          <>
                            {message.summary ? (
                              <>
                                <p className="mt-2 whitespace-pre-wrap text-sm text-[#d1d4dc]">
                                  {message.summary}
                                </p>
                                {isLatestAssistantCode ? (
                                  <button
                                    type="button"
                                    className="mt-2 rounded border border-[#26a69a]/60 px-3 py-1 text-xs text-[#26a69a] transition hover:bg-[#26a69a]/15 disabled:opacity-50"
                                  onClick={() => void handleSummaryExpand()}
                                  disabled={isSending}
                                >
                                    Expand summary
                                  </button>
                                ) : null}
                              </>
                            ) : null}
                            <details className="mt-2">
                              <summary className="cursor-pointer text-xs text-[#868993]">
                                Code
                              </summary>
                              <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs text-[#d1d4dc]">
                                {message.content}
                              </pre>
                            </details>
                            {message.backtest_ok ? (
                              <p className="mt-2 text-xs text-[#26a69a]">
                                Backtest passed. Strategy runs correctly.
                              </p>
                            ) : null}
                            {message.repaired ? (
                              <p className="mt-1 text-xs text-[#f9a825]">
                                Auto-fix applied and verification passed ({message.repair_attempts ?? 0} attempts)
                              </p>
                            ) : null}
                            {message.content && !message.path && isLatestAssistantCode ? (
                              <div className="mt-2 flex flex-wrap items-center gap-2">
                                <button
                                  type="button"
                                  className="rounded border border-[#2962ff] px-3 py-1 text-xs text-[#2962ff] transition hover:bg-[#2962ff] hover:text-white disabled:opacity-50"
                                  onClick={() => void handleSaveClick(message.id, message.content)}
                                  disabled={savingId !== null}
                                >
                                  Save strategy
                                </button>
                              </div>
                            ) : null}
                            <div className="mt-2 space-y-1 text-xs text-[#868993]">
                              {message.path ? <div>Saved to Strategy Library</div> : null}
                              {message.model ? <div>Model: {message.model}</div> : null}
                            </div>
                          </>
                        )
                      ) : (
                        <p className="mt-2 text-sm text-[#d1d4dc]">{message.content}</p>
                      )}
                    </div>
                    );
                  })}
                  </div>
                </div>
                <div className="flex-shrink-0 border-t border-[#2a2e39] px-4 py-4">
                  <form
                    className="mx-auto max-w-3xl flex gap-2 rounded-xl border border-[#2a2e39] bg-[#131722] p-2"
                    onSubmit={handleSubmit}
                  >
                    <textarea
                      className="min-h-[44px] max-h-[200px] flex-1 resize-none bg-transparent px-3 py-2 text-sm text-[#d1d4dc] placeholder:text-[#5f6472] focus:outline-none"
                      onChange={(e) => setPrompt(e.target.value)}
                      onCompositionStart={() => setIsComposingPrompt(true)}
                      onCompositionEnd={() => setIsComposingPrompt(false)}
                      onKeyDown={handleKeyDown}
                      placeholder="Describe your strategy..."
                      value={prompt}
                      rows={1}
                    />
                    <button
                      className="rounded-lg bg-[#2962ff] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#2a52e0] disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={!prompt.trim() || isSending}
                      type="submit"
                    >
                      {isSending ? "..." : "Generate"}
                    </button>
                  </form>
                  <p className="mx-auto mt-2 max-w-3xl text-xs text-[#868993]">
                    Note: execution settings in the Backtest/Live forms override values mentioned in this prompt.
                  </p>
                </div>
              </>
            ) : (
              <>
                {chatError ? (
                  <p className="mx-4 mt-4 flex-shrink-0 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
                    {chatError}
                  </p>
                ) : null}
            <div className="flex flex-1 flex-col items-center justify-center px-4 py-12">
              <div className="mb-4 w-full max-w-2xl rounded-xl border border-[#2a2e39] bg-[#131722] p-4">
                <h3 className="text-sm font-semibold text-[#d1d4dc]">Continue From Existing Strategy</h3>
                <p className="mt-1 text-xs text-[#868993]">
                  Load a saved strategy into the workspace and get a quick LLM summary.
                </p>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                  <select
                    className="w-full rounded border border-[#2a2e39] bg-[#171b25] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                    value={loadStrategyPath}
                    onChange={(e) => setLoadStrategyPath(e.target.value)}
                    disabled={items.length === 0 || isLoadingStrategy || isSending}
                  >
                    {items.length === 0 ? (
                      <option value="">No saved strategies</option>
                    ) : (
                      items.map((item) => (
                        <option key={`load-${item.path}`} value={item.path}>
                          {strategyNameFromPath(item.name)}
                        </option>
                      ))
                    )}
                  </select>
                  <button
                    type="button"
                    className="rounded border border-[#2962ff] px-3 py-2 text-sm text-[#2962ff] transition hover:bg-[#2962ff] hover:text-white disabled:opacity-50"
                    onClick={() => void handleLoadStrategy()}
                    disabled={!loadStrategyPath || items.length === 0 || isLoadingStrategy || isSending}
                  >
                    {isLoadingStrategy ? "Loading..." : "Load"}
                  </button>
                </div>
                {loadStrategyError ? (
                  <p className="mt-2 text-xs text-[#ef5350]">{loadStrategyError}</p>
                ) : null}
              </div>
              <form
                className="w-full max-w-2xl"
                onSubmit={handleSubmit}
              >
                    <div className="rounded-2xl border border-[#2a2e39] bg-[#131722] p-3 shadow-lg">
                      <textarea
                        className="min-h-[120px] w-full resize-none bg-transparent px-3 py-2 text-sm text-[#d1d4dc] placeholder:text-[#5f6472] focus:outline-none"
                        onChange={(e) => setPrompt(e.target.value)}
                        onCompositionStart={() => setIsComposingPrompt(true)}
                        onCompositionEnd={() => setIsComposingPrompt(false)}
                        onKeyDown={handleKeyDown}
                        placeholder="Describe your strategy in plain language. e.g. Buy when RSI crosses above 30, sell at 70, 1h candles."
                        value={prompt}
                      />
                      <div className="flex justify-end pt-2">
                        <button
                          className="rounded-lg bg-[#2962ff] px-5 py-2 text-sm font-medium text-white transition hover:bg-[#2a52e0] disabled:cursor-not-allowed disabled:opacity-60"
                          disabled={!prompt.trim() || isSending}
                          type="submit"
                        >
                          {isSending ? "Generating..." : "Generate"}
                        </button>
                      </div>
                    </div>
                    <p className="mt-2 text-xs text-[#868993]">
                      Note: execution settings in the Backtest/Live forms override values mentioned in this prompt.
                    </p>
                  </form>
                </div>
              </>
            )}
          </div>

          {workspaceOpen ? (
            <div
              className="hidden w-1 shrink-0 cursor-col-resize bg-[#2a2e39] transition hover:bg-[#2962ff] lg:block"
              onMouseDown={handleWorkspaceResizeStart}
            />
          ) : null}
          <aside
            className={`relative hidden shrink-0 border-l border-[#2a2e39] bg-[#151924] lg:flex lg:flex-col ${
              workspaceOpen ? "" : "overflow-hidden border-l-0"
            }`}
            style={{ width: workspaceOpen ? workspaceWidth : 0 }}
          >
            {workspaceOpen ? (
              <>
                <div className="flex items-center justify-between border-b border-[#2a2e39] px-3 py-3">
                  <div>
                    <h3 className="text-sm font-semibold text-[#d1d4dc]">Workspace Code</h3>
                    <p className="mt-1 text-[11px] text-[#868993]">
                      Follow-up generation uses this code automatically.
                    </p>
                  </div>
                  <button
                    type="button"
                    className="rounded border border-[#2962ff]/70 px-2 py-1 text-xs text-[#8fa8ff] transition hover:bg-[#2962ff]/15 disabled:opacity-50"
                    onClick={handleSaveWorkspace}
                    disabled={!workspaceCode.trim() || savingId !== null}
                  >
                    Save
                  </button>
                </div>

                <div className="min-h-0 flex-1">
                  {workspaceCode.trim() ? (
                    <div className="flex h-full overflow-hidden">
                      <div
                        ref={workspaceGutterRef}
                        className="w-14 overflow-y-auto border-r border-[#2a2e39] bg-[#131722] py-3 text-right font-mono text-xs leading-6 text-[#5f6472]"
                      >
                        {Array.from({ length: workspaceLineCount }, (_, idx) => {
                          const lineNo = idx + 1;
                          return (
                            <div
                              key={`workspace-line-${lineNo}`}
                              className={`pr-2 ${lineNo === syntaxErrorLine ? "bg-[#3b1f26] text-[#ef9a9a]" : ""}`}
                            >
                              {lineNo}
                            </div>
                          );
                        })}
                      </div>
                      <textarea
                        ref={workspaceTextAreaRef}
                        className="h-full flex-1 resize-none bg-transparent px-3 py-3 font-mono text-xs leading-6 text-[#d1d4dc] focus:outline-none"
                        spellCheck={false}
                        value={workspaceCode}
                        onChange={(e) => handleWorkspaceChange(e.target.value)}
                        onScroll={handleWorkspaceScroll}
                      />
                    </div>
                  ) : (
                    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-[#868993]">
                      전략 코드가 생성되면 이 영역에서 계속 편집할 수 있습니다.
                    </div>
                  )}
                </div>

                <div className="border-t border-[#2a2e39] px-3 py-2 text-xs">
                  {workspaceChecking ? (
                    <span className="text-[#8fa8ff]">Checking syntax...</span>
                  ) : workspaceSyntaxError ? (
                    <span className="text-[#ef9a9a]">Syntax check failed: {workspaceSyntaxError}</span>
                  ) : workspaceSyntax?.valid ? (
                    <span className="text-[#7fd4a6]">No syntax errors found</span>
                  ) : workspaceSyntax?.error ? (
                    <span className="text-[#ef9a9a]">
                      Syntax error: {workspaceSyntax.error.message}
                      {syntaxErrorLine ? ` (line ${syntaxErrorLine}` : ""}
                      {syntaxErrorColumn ? `, col ${syntaxErrorColumn}` : ""}
                      {syntaxErrorLine ? ")" : ""}
                    </span>
                  ) : (
                    <span className="text-[#868993]">Syntax check runs while you edit code.</span>
                  )}
                  {workspaceDirty ? (
                    <p className="mt-1 text-[11px] text-[#f9a825]">
                      Unsaved edits in workspace
                    </p>
                  ) : null}
                  {initialGeneratedCode ? (
                    <details className="mt-2 rounded border border-[#2a2e39] bg-[#101522] p-2">
                      <summary className="cursor-pointer text-[11px] text-[#9aa0ad]">
                        Diff from initial code {hasWorkspaceDiff ? "(modified)" : "(no changes)"}
                      </summary>
                      <div className="mt-2 max-h-48 overflow-auto rounded border border-[#2a2e39] bg-[#0d111a] font-mono text-[11px] leading-5">
                        {workspaceDiffLines.map((line, idx) => {
                          const prefix = line.type === "add" ? "+" : line.type === "remove" ? "-" : " ";
                          const rowClass =
                            line.type === "add"
                              ? "bg-[#1a2f25] text-[#8ad0a4]"
                              : line.type === "remove"
                                ? "bg-[#3a1f26] text-[#f3a6ae]"
                                : "text-[#8690a3]";
                          return (
                            <div key={`diff-${idx}`} className={`grid grid-cols-[56px_1fr] px-2 ${rowClass}`}>
                              <span className="select-none text-[#6b7383]">
                                {line.leftLineNo ?? ""}{line.rightLineNo ? `:${line.rightLineNo}` : ""}
                              </span>
                              <span className="whitespace-pre-wrap break-words">
                                {prefix} {line.text}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  ) : null}
                </div>
              </>
            ) : null}
          </aside>
          <button
            type="button"
            className="absolute right-2 top-1/2 z-20 hidden -translate-y-1/2 rounded-full border border-[#2a2e39] bg-[#171b25] px-2 py-3 text-xs text-[#9aa0ad] shadow-lg transition hover:border-[#2962ff] hover:text-white lg:block"
            onClick={handleWorkspaceToggle}
            aria-label={workspaceOpen ? "Collapse workspace" : "Expand workspace"}
          >
            {workspaceOpen ? ">" : "<"}
          </button>
        </div>

        {saveModal ? (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
            role="dialog"
            aria-modal="true"
          >
            <div className="w-full max-w-md rounded-xl border border-[#2a2e39] bg-[#1e222d] p-6 shadow-xl">
              <h3 className="text-lg font-semibold text-[#d1d4dc]">Save strategy</h3>
              <p className="mt-1 text-sm text-[#868993]">
                Enter a name for this strategy file (optional).
              </p>
              <input
                className="mt-4 w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder:text-[#5f6472] focus:border-[#2962ff] focus:outline-none"
                onChange={(e) =>
                  setSaveModal((prev) => (prev ? { ...prev, name: e.target.value } : null))
                }
                placeholder="e.g. rsi_reversal"
                value={saveModal.name}
              />
              <div className="mt-6 flex justify-end gap-2">
                <button
                  type="button"
                  className="rounded border border-[#2a2e39] px-4 py-2 text-sm text-[#d1d4dc] transition hover:bg-[#2a2e39]"
                  onClick={() => setSaveModal(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="rounded bg-[#2962ff] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#2a52e0] disabled:opacity-60"
                  disabled={savingId !== null}
                  onClick={() => void handleSaveConfirm()}
                >
                  {savingId ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </div>
        ) : null}

      </section>
      ) : (
      <section className="mt-0 min-h-0 flex-1 overflow-y-auto rounded-b-lg border border-t-0 border-[#2a2e39] bg-[#1e222d] p-6">
        <h2 className="text-lg font-semibold text-[#d1d4dc]">Saved Strategies</h2>
        {error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {error}
          </p>
        ) : null}
        {deleteError && deletingPath ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {deleteError}
          </p>
        ) : null}
        {items.length === 0 && !error ? (
          <div className="mt-6 rounded border border-[#2a2e39] bg-[#131722] px-4 py-8 text-center text-sm text-[#868993]">
            No strategies found.
          </div>
        ) : (
          <div className="mt-6 flex flex-col gap-2">
            {items.map((s) => (
              <div
                key={s.path}
                className="flex items-center justify-between gap-3 rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 transition-colors hover:border-[#2962ff] hover:bg-[#252936]"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-[#d1d4dc]">{s.name}</div>
                  <div className="truncate text-xs text-[#868993]">Ready to run</div>
                </div>
                <button
                  type="button"
                  className="shrink-0 rounded border border-[#ef5350]/50 px-3 py-1.5 text-xs text-[#ef5350] transition hover:border-[#ef5350] hover:bg-[#ef5350]/10"
                  onClick={() => handleDeleteClick(s.path)}
                >
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
        {deletingPath ? (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
            role="dialog"
            aria-modal="true"
          >
            <div className="w-full max-w-md rounded-xl border border-[#2a2e39] bg-[#1e222d] p-6 shadow-xl">
              <h3 className="text-lg font-semibold text-[#d1d4dc]">Delete Strategy</h3>
              <p className="mt-1 text-sm text-[#868993]">
                Delete this strategy from the library? This action cannot be undone.
              </p>
              <p className="mt-2 truncate rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 font-mono text-xs text-[#d1d4dc]">
                {strategyNameFromPath(deletingPath)}
              </p>
              <div className="mt-6 flex justify-end gap-2">
                <button
                  type="button"
                  className="rounded border border-[#2a2e39] px-4 py-2 text-sm text-[#d1d4dc] transition hover:bg-[#2a2e39]"
                  onClick={() => {
                    setDeletingPath(null);
                    setDeleteError(null);
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="rounded bg-[#ef5350] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#d32f2f]"
                  onClick={() => void handleDeleteConfirm()}
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </section>
      )}
    </main>
  );
}
