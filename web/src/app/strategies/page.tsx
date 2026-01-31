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
  const [strategyName, setStrategyName] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
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
        strategyName,
        messagesToSend,
      );
    } catch (e) {
      setChatError(String(e));
      setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
      setIsSending(false);
    }
  };

  const handleSave = async (messageId: string, code: string) => {
    if (savingId) return;
    setSavingId(messageId);
    setChatError(null);
    try {
      const result = await saveStrategy(code, strategyName);
      setChatMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, path: result.path } : m)),
      );
      listStrategies()
        .then(setItems)
        .catch((e) => setError(String(e)));
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
    setStrategyName("");
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
    <main className="mx-auto max-w-5xl px-6 py-10">
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
      <section className="mt-0 flex min-h-[60vh] flex-col rounded-b-lg border border-t-0 border-[#2a2e39] bg-[#1e222d] p-6">
        <div className="flex flex-shrink-0 flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-[#d1d4dc]">Strategy Chat</h2>
            <p className="mt-1 text-sm text-[#868993]">
              Describe a strategy in plain language and generate a Python draft.
            </p>
          </div>
          <button
            className="rounded border border-[#2a2e39] px-3 py-1 text-xs text-[#d1d4dc] transition hover:border-[#2962ff] hover:text-white"
            onClick={handleClear}
            type="button"
          >
            Clear
          </button>
        </div>

        {chatError ? (
          <p className="mt-4 flex-shrink-0 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {chatError}
          </p>
        ) : null}

        <div
          ref={chatScrollRef}
          className="mt-4 min-h-0 flex-1 overflow-y-auto rounded border border-[#2a2e39] bg-[#131722] p-4"
        >
          {chatMessages.length === 0 ? (
            <div className="text-sm text-[#868993]">
              No messages yet. Start with a short strategy description.
            </div>
          ) : (
            <div className="space-y-3">
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
                          onClick={() => void handleSave(message.id, message.content)}
                          disabled={savingId !== null}
                        >
                          {savingId === message.id ? "Saving..." : "Save strategy"}
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
          )}
        </div>

        <form className="mt-4 flex-shrink-0" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-3 md:flex-row">
            <div className="flex flex-1 flex-col gap-2">
              <input
                className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder:text-[#5f6472] focus:border-[#2962ff] focus:outline-none"
                onChange={(event) => setStrategyName(event.target.value)}
                placeholder="Optional file name (e.g. rsi_reversal)"
                value={strategyName}
              />
              <textarea
                className="min-h-[110px] resize-none rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder:text-[#5f6472] focus:border-[#2962ff] focus:outline-none"
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="e.g. Buy when RSI crosses above 30, sell at 70, 1h candles."
                value={prompt}
              />
            </div>
            <div className="flex flex-col gap-2 md:w-44">
              <button
                className="rounded bg-[#2962ff] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[#2a52e0] disabled:cursor-not-allowed disabled:opacity-60"
                disabled={!prompt.trim() || isSending}
                type="submit"
              >
                {isSending ? "Generating..." : "Generate"}
              </button>
              <div className="text-xs text-[#868993]">Enter to send, Shift+Enter for new line.</div>
            </div>
          </div>
        </form>
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
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((s) => (
              <div
                key={s.path}
                className="rounded-lg border border-[#2a2e39] bg-[#131722] p-5 transition-colors hover:border-[#2962ff] hover:bg-[#252936]"
              >
                <div className="font-medium text-[#d1d4dc]">{s.name}</div>
                <div className="mt-1 font-mono text-xs text-[#868993]">{s.path}</div>
              </div>
            ))}
          </div>
        )}
      </section>
      )}
    </main>
  );
}
