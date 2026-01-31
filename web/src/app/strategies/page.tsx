"use client";

import { useEffect, useRef, useState } from "react";

import { generateStrategyStream, listStrategies, saveStrategy } from "@/lib/api";
import type { StrategyInfo } from "@/lib/types";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  model?: string | null;
  path?: string | null;
  summary?: string | null;
  backtest_ok?: boolean;
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

    const useMultiturn = nextMessages.length > 1;
    const messagesToSend = useMultiturn
      ? nextMessages.map((m) => ({ role: m.role, content: m.content }))
      : undefined;

    const assistantId = createId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      model: null,
      path: null,
      summary: null,
      backtest_ok: false,
    };
    setChatMessages((prev) => [...prev, assistantMessage]);

    try {
      await generateStrategyStream(
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
    } catch (e) {
      setChatError(String(e));
      setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
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
        {items.length === 0 && !error ? (
          <div className="mt-6 rounded border border-[#2a2e39] bg-[#131722] px-4 py-8 text-center text-sm text-[#868993]">
            No strategies found.
          </div>
        ) : (
          <div className="mt-6 flex flex-col gap-2">
            {items.map((s) => (
              <div
                key={s.path}
                className="rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 transition-colors hover:border-[#2962ff] hover:bg-[#252936]"
              >
                <div className="font-medium text-[#d1d4dc]">{s.name}</div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}
    </main>
  );
}
