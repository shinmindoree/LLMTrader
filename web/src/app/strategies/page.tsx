"use client";

import { useEffect, useRef, useState } from "react";

import {
  deleteStrategy,
  getStrategyCapabilities,
  generateStrategyStream,
  intakeStrategy,
  listStrategies,
  saveStrategy,
  strategyChat,
  validateStrategySyntax,
} from "@/lib/api";
import type {
  StrategyCapabilitiesResponse,
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

const CONTEXT_METHOD_LABELS: Record<string, string> = {
  current_price: "Current market price",
  position_size: "Current position size",
  position_entry_price: "Average entry price",
  unrealized_pnl: "Unrealized PnL",
  balance: "Available balance",
  buy: "Market buy",
  sell: "Market sell",
  close_position: "Close position",
  calc_entry_quantity: "Auto-calculate order size",
  enter_long: "Open long position",
  enter_short: "Open short position",
  get_indicator: "Read indicator values",
  register_indicator: "Register custom indicators",
  get_open_orders: "View open orders",
};

const UNSUPPORTED_CATEGORY_LABELS: Record<string, string> = {
  social_stream: "Social data",
  news_feed: "News feed",
  sentiment_engine: "Sentiment signals",
  onchain_feed: "On-chain data",
  macro_feed: "Macro data",
};

function uniqueNonEmpty(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of items) {
    const normalized = item.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

function formatDataSourceLabel(source: string): string {
  const normalized = source.toLowerCase();
  if (normalized.includes("binance") && normalized.includes("ohlcv")) {
    return "Binance candle data (OHLCV)";
  }
  if (normalized.includes("ohlcv")) {
    return "Candle data (OHLCV)";
  }
  return source.trim();
}

function formatIndicatorScopeLabels(scopes: string[]): string[] {
  let supportsTalib = false;
  let supportsCustom = false;
  const fallback: string[] = [];

  for (const scope of scopes) {
    const normalized = scope.toLowerCase();
    if (
      normalized.includes("ta-lib") ||
      normalized.includes("talib") ||
      normalized.includes("builtin")
    ) {
      supportsTalib = true;
    }
    if (normalized.includes("custom") || normalized.includes("register_indicator")) {
      supportsCustom = true;
    }
    if (
      !normalized.includes("ctx.get_indicator") &&
      !normalized.includes("ctx.register_indicator")
    ) {
      fallback.push(scope.trim());
    }
  }

  const labels: string[] = [];
  if (supportsTalib) labels.push("TA-Lib built-in indicators");
  if (supportsCustom) labels.push("Custom indicators in strategy code");
  return uniqueNonEmpty([...labels, ...fallback]);
}

function prettifyMethodName(raw: string): string {
  const value = raw.trim().replace(/_/g, " ");
  if (!value) return "";
  return value.slice(0, 1).toUpperCase() + value.slice(1);
}

function formatContextMethodLabels(methods: string[]): string[] {
  const expanded = methods.flatMap((method) =>
    method
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
  return uniqueNonEmpty(
    expanded.map((method) => CONTEXT_METHOD_LABELS[method] ?? prettifyMethodName(method)),
  );
}

function formatUnsupportedCategoryLabel(category: string): string {
  const normalized = category.trim();
  return UNSUPPORTED_CATEGORY_LABELS[normalized] ?? normalized.replace(/_/g, " ");
}

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";

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

const createId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;

type TabId = "chat" | "list";
const SUMMARY_EXPAND_PROMPT =
  "방금 전략 요약을 이어서 더 자세히 설명해줘. 전략 개요 → 진입 흐름 → 청산 흐름 → 리스크 관리 → 실전 주의사항 순서로 써줘. 코드는 변경하지 마.";

export default function StrategiesPage() {
  const [activeTab, setActiveTab] = useState<TabId>("chat");
  const [items, setItems] = useState<StrategyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<StrategyCapabilitiesResponse | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
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
  const [workspaceCode, setWorkspaceCode] = useState("");
  const [workspaceSourceMessageId, setWorkspaceSourceMessageId] = useState<string | null>(null);
  const [workspaceSummary, setWorkspaceSummary] = useState<string | null>(null);
  const [workspaceDirty, setWorkspaceDirty] = useState(false);
  const [workspaceChecking, setWorkspaceChecking] = useState(false);
  const [workspaceSyntax, setWorkspaceSyntax] = useState<StrategySyntaxCheckResponse | null>(null);
  const [workspaceSyntaxError, setWorkspaceSyntaxError] = useState<string | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const workspaceGutterRef = useRef<HTMLDivElement | null>(null);
  const workspaceTextAreaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    listStrategies()
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    getStrategyCapabilities()
      .then(setCapabilities)
      .catch((e) => setCapabilityError(String(e)));
  }, []);

  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [chatMessages]);

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

  const handleSaveWorkspace = () => {
    const code = workspaceCode.trim();
    if (!code) return;
    setSaveModal({
      messageId: workspaceSourceMessageId ?? createId(),
      code,
      name: "",
    });
  };

  const handleLoadLatestGeneratedCode = () => {
    const latest = [...chatMessages]
      .reverse()
      .find((m) => m.role === "assistant" && !m.textOnly && Boolean(m.content));
    if (!latest) return;
    setWorkspaceCode(latest.content);
    setWorkspaceSourceMessageId(latest.id);
    setWorkspaceSummary(latest.summary ?? null);
    setWorkspaceDirty(false);
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
      setChatMessages([]);
      setSaveModal(null);
      setActiveTab("list");
    } catch (e) {
      setChatError(String(e));
    } finally {
      setSavingId(null);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const handleClear = () => {
    setChatMessages([]);
    setChatError(null);
    setPrompt("");
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

  const dataSourceLabels = capabilities
    ? uniqueNonEmpty(capabilities.supported_data_sources.map(formatDataSourceLabel))
    : [];
  const indicatorLabels = capabilities
    ? formatIndicatorScopeLabels(capabilities.supported_indicator_scopes)
    : [];
  const contextMethodLabels = capabilities
    ? formatContextMethodLabels(capabilities.supported_context_methods)
    : [];
  const unsupportedLabels = capabilities
    ? uniqueNonEmpty(capabilities.unsupported_categories.map(formatUnsupportedCategoryLabel))
    : [];
  const latestAssistantCodeId =
    [...chatMessages]
      .reverse()
      .find((m) => m.role === "assistant" && !m.textOnly && Boolean(m.content))?.id ?? null;
  const workspaceLineCount = Math.max(1, workspaceCode.split("\n").length);
  const syntaxErrorLine = workspaceSyntax?.error?.line ?? null;
  const syntaxErrorColumn =
    typeof workspaceSyntax?.error?.column === "number" ? workspaceSyntax.error.column + 1 : null;

  return (
    <main className="w-full px-6 py-10">
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
      <section className="mt-0 flex min-h-[60vh] flex-col rounded-b-lg border border-t-0 border-[#2a2e39] bg-[#1e222d]">
        <div className="border-b border-[#2a2e39] bg-[#171b25] px-4 py-3">
          <div className="mx-auto max-w-5xl">
            <h2 className="text-sm font-semibold text-[#d1d4dc]">Current Strategy Generation Scope</h2>
            {capabilityError ? (
              <p className="mt-2 text-xs text-[#ef5350]">
                Failed to load capability info: {capabilityError}
              </p>
            ) : capabilities ? (
              <>
                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
                    <h3 className="text-xs font-semibold text-[#d1d4dc]">Market Data</h3>
                    <p className="mt-1 text-xs text-[#868993]">
                      Strategies are generated using the data sources below.
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {dataSourceLabels.length > 0 ? (
                        dataSourceLabels.map((item) => (
                          <span
                            key={`data-${item}`}
                            className="rounded border border-[#2a2e39] bg-[#171b25] px-2 py-1 text-xs text-[#9aa0ad]"
                          >
                            {item}
                          </span>
                        ))
                      ) : (
                        <p className="text-xs text-[#868993]">Loading available data sources...</p>
                      )}
                    </div>
                  </div>
                  <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
                    <h3 className="text-xs font-semibold text-[#d1d4dc]">Indicator Support</h3>
                    <p className="mt-1 text-xs text-[#868993]">
                      You can use both built-in and custom indicators.
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {indicatorLabels.length > 0 ? (
                        indicatorLabels.map((item) => (
                          <span
                            key={`indicator-${item}`}
                            className="rounded border border-[#2962ff]/30 bg-[#0f1b3a] px-2 py-1 text-xs text-[#8fa8ff]"
                          >
                            {item}
                          </span>
                        ))
                      ) : (
                        <p className="text-xs text-[#868993]">Loading indicator capabilities...</p>
                      )}
                    </div>
                  </div>
                  <div className="rounded border border-[#2a2e39] bg-[#131722] p-3 md:col-span-2">
                    <h3 className="text-xs font-semibold text-[#d1d4dc]">Execution Controls</h3>
                    <p className="mt-1 text-xs text-[#868993]">
                      These controls are available for entries, exits, and position management.
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {contextMethodLabels.length > 0 ? (
                        contextMethodLabels.map((item) => (
                          <span
                            key={`method-${item}`}
                            className="rounded border border-[#2a2e39] bg-[#171b25] px-2 py-1 text-xs text-[#9aa0ad]"
                          >
                            {item}
                          </span>
                        ))
                      ) : (
                        <p className="text-xs text-[#868993]">Loading execution controls...</p>
                      )}
                    </div>
                  </div>
                  {unsupportedLabels.length > 0 ? (
                    <div className="rounded border border-[#f9a825]/30 bg-[#2b2417] p-3 md:col-span-2">
                      <h3 className="text-xs font-semibold text-[#f9a825]">Currently Unsupported</h3>
                      <p className="mt-1 text-xs text-[#d7b36a]">
                        Items requiring external integrations are outside the current generation scope.
                      </p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {unsupportedLabels.map((item) => (
                          <span
                            key={`unsupported-${item}`}
                            className="rounded border border-[#f9a825]/40 bg-[#2f2718] px-2 py-1 text-xs text-[#f7c65e]"
                          >
                            {item}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="mt-2 text-xs text-[#868993]">Loading capability info...</p>
            )}
          </div>
        </div>
        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1 flex flex-col">
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
                  <form
                    className="w-full max-w-2xl"
                    onSubmit={handleSubmit}
                  >
                    <div className="rounded-2xl border border-[#2a2e39] bg-[#131722] p-3 shadow-lg">
                      <textarea
                        className="min-h-[120px] w-full resize-none bg-transparent px-3 py-2 text-sm text-[#d1d4dc] placeholder:text-[#5f6472] focus:outline-none"
                        onChange={(e) => setPrompt(e.target.value)}
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

          <aside className="hidden w-[420px] shrink-0 border-l border-[#2a2e39] bg-[#151924] lg:flex lg:flex-col">
            <div className="flex items-center justify-between border-b border-[#2a2e39] px-3 py-3">
              <div>
                <h3 className="text-sm font-semibold text-[#d1d4dc]">Workspace Code</h3>
                <p className="mt-1 text-[11px] text-[#868993]">
                  Follow-up improvements are based on this code.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#9aa0ad] transition hover:border-[#2962ff] hover:text-white"
                  onClick={handleLoadLatestGeneratedCode}
                >
                  Load latest
                </button>
                <button
                  type="button"
                  className="rounded border border-[#2962ff]/70 px-2 py-1 text-xs text-[#8fa8ff] transition hover:bg-[#2962ff]/15 disabled:opacity-50"
                  onClick={handleSaveWorkspace}
                  disabled={!workspaceCode.trim() || savingId !== null}
                >
                  Save
                </button>
              </div>
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
            </div>
          </aside>
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
      <section className="mt-0 rounded-b-lg border border-t-0 border-[#2a2e39] bg-[#1e222d] p-6">
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
