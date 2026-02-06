"use client";

import { useEffect, useRef, useState } from "react";

import {
  deleteStrategy,
  generateStrategyStream,
  intakeStrategy,
  listStrategies,
  saveStrategy,
  strategyChat,
} from "@/lib/api";
import type { StrategyInfo, StrategyIntakeResponse } from "@/lib/types";

const MODIFY_KEYWORDS =
  /수정|바꿔|변경|추가해|제거|고쳐|change|modify|update|add|remove|바꿔줘|수정해줘|변경해줘|다시\s*만들|재생성|regenerate/i;

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

function formatIntakeGuidance(intake: StrategyIntakeResponse): string {
  const lines: string[] = [intake.user_message];
  if (intake.clarification_questions.length > 0) {
    lines.push("", "추가로 필요한 정보:");
    intake.clarification_questions.forEach((q, idx) => {
      lines.push(`${idx + 1}. ${q}`);
    });
  }
  if (intake.unsupported_requirements.length > 0) {
    lines.push("", "현재 미지원 항목:");
    intake.unsupported_requirements.forEach((item) => {
      lines.push(`- ${item}`);
    });
  }
  return lines.join("\n");
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
};

const createId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;

type TabId = "chat" | "list";

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
  const chatScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    listStrategies()
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [chatMessages]);

  const handleSubmit = async (event?: React.FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed || isSending) {
      return;
    }

    setChatError(null);
    setPrompt("");
    setIsSending(true);
    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: trimmed,
    };
    const nextMessages = [...chatMessages, userMessage];
    setChatMessages(nextMessages);

    const lastCodeSummary = getLastCodeAndSummary(nextMessages);
    const isFirstTurn = !lastCodeSummary;
    const isModify = lastCodeSummary && isModifyIntent(trimmed);

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
              return [...prev.slice(0, -1), { ...last, content: last.content + token }];
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
                  },
                ];
              });
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
    };

    if (isFirstTurn || isModify) {
      setChatMessages((prev) => [...prev, assistantMessage]);
      try {
        const messagesToSend = nextMessages.length > 1 ? toApiMessages(nextMessages) : undefined;
        const intake = await intakeStrategy(trimmed, messagesToSend);
        if (intake.status !== "READY") {
          const guidance = formatIntakeGuidance(intake);
          setChatMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role !== "assistant" || last.id !== assistantId) return prev;
            return [...prev.slice(0, -1), { ...last, content: guidance, textOnly: true }];
          });
          setIsSending(false);
          return;
        }
        await doGenerate(messagesToSend, intake.normalized_spec);
      } catch (e) {
        setChatError(String(e));
        setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
        setIsSending(false);
      }
      return;
    }

    setChatMessages((prev) => [...prev, { ...assistantMessage, textOnly: true }]);
    try {
      const chatMessagesForApi = toApiMessages(nextMessages);
      const res = await strategyChat(
        lastCodeSummary.code,
        lastCodeSummary.summary,
        chatMessagesForApi,
      );
      setChatMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role !== "assistant" || last.id !== assistantId) return prev;
        return [
          ...prev.slice(0, -1),
          { ...last, content: res.content, textOnly: true },
        ];
      });
    } catch (e) {
      setChatError(String(e));
      setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
    } finally {
      setIsSending(false);
    }
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
              {chatMessages.map((message) => (
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
                  {message.role === "assistant" ? (
                    message.textOnly ? (
                      <p className="mt-2 whitespace-pre-wrap text-sm text-[#d1d4dc]">
                        {message.content}
                      </p>
                    ) : (
                      <>
                        {message.summary ? (
                          <p className="mt-2 whitespace-pre-wrap text-sm text-[#d1d4dc]">
                            {message.summary}
                          </p>
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
                            자동 수정 후 검증 통과 ({message.repair_attempts ?? 0}회 시도)
                          </p>
                        ) : null}
                        {message.content &&
                        !message.path &&
                        chatMessages.filter((m) => m.role === "assistant").pop()?.id ===
                          message.id ? (
                          <button
                            type="button"
                            className="mt-2 rounded border border-[#2962ff] px-3 py-1 text-xs text-[#2962ff] transition hover:bg-[#2962ff] hover:text-white disabled:opacity-50"
                            onClick={() => void handleSaveClick(message.id, message.content)}
                            disabled={savingId !== null}
                          >
                            Save strategy
                          </button>
                        ) : null}
                        <div className="mt-2 space-y-1 text-xs text-[#868993]">
                          {message.path ? <div>Saved: {message.path}</div> : null}
                          {message.model ? <div>Model: {message.model}</div> : null}
                        </div>
                      </>
                    )
                  ) : (
                    <p className="mt-2 text-sm text-[#d1d4dc]">{message.content}</p>
                  )}
                </div>
              ))}
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
                참고: 프롬프트에 레버리지/심볼/간격 등 거래 설정을 적어도, 실제 백테스트/라이브 실행 시에는
                실행 폼에서 입력한 설정값이 우선 적용됩니다.
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
                  참고: 프롬프트에 레버리지/심볼/간격 등 거래 설정을 적어도, 실제 백테스트/라이브 실행 시에는
                  실행 폼에서 입력한 설정값이 우선 적용됩니다.
                </p>
              </form>
            </div>
          </>
        )}

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
                  <div className="truncate text-xs text-[#868993]">{s.path}</div>
                </div>
                <button
                  type="button"
                  className="shrink-0 rounded border border-[#ef5350]/50 px-3 py-1.5 text-xs text-[#ef5350] transition hover:border-[#ef5350] hover:bg-[#ef5350]/10"
                  onClick={() => handleDeleteClick(s.path)}
                >
                  삭제
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
              <h3 className="text-lg font-semibold text-[#d1d4dc]">전략 삭제</h3>
              <p className="mt-1 text-sm text-[#868993]">
                이 전략 파일을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.
              </p>
              <p className="mt-2 truncate rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 font-mono text-xs text-[#d1d4dc]">
                {deletingPath}
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
                  취소
                </button>
                <button
                  type="button"
                  className="rounded bg-[#ef5350] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#d32f2f]"
                  onClick={() => void handleDeleteConfirm()}
                >
                  삭제
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
