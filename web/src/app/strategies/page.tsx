"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "@/lib/i18n";

import {
  deleteStrategyChatSession,
  deleteStrategy,
  getStrategyContent,
  generateStrategyStream,
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
  StrategySyntaxCheckResponse,
} from "@/lib/types";

function looksLikePythonCode(content: string): boolean {
  const text = content.trim();
  if (!text) return false;

  const strongSignals = [
    /^\s*from\s+[A-Za-z_][\w.]*\s+import\s+.+/m,
    /^\s*import\s+[A-Za-z_][\w.]*/m,
    /^\s*(async\s+def|def|class)\s+[A-Za-z_]\w*/m,
    /^\s*if __name__ == ["']__main__["']:/m,
    /^\s*@\w+/m,
  ];

  if (strongSignals.some((pattern) => pattern.test(text))) {
    return true;
  }

  const controlFlowSignals = text.match(/^\s*(if|elif|else|for|while|try|except|with)\b.*:\s*$/gm);
  const assignmentSignals = text.match(/^\s*[A-Za-z_]\w*\s*=\s*.+$/gm);
  return Boolean(controlFlowSignals?.length && assignmentSignals?.length);
}

function getLastCodeAndSummary(messages: ChatMessage[]): {
  code: string;
  summary: string | null;
} | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "assistant" && m.content && !m.textOnly && looksLikePythonCode(m.content)) {
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
  previousCodeContext: string,
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
      content: previousCodeContext + latestCode,
    },
    last,
  ];
  return out;
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

type PromptComposerProps = {
  centered?: boolean;
  disabled?: boolean;
  isSending: boolean;
  onChange: (value: string) => void;
  onCompositionEnd: () => void;
  onCompositionStart: () => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (event?: React.FormEvent<HTMLFormElement>) => void | Promise<void>;
  placeholder: string;
  prompt: string;
};

type RichTextBlock =
  | { type: "paragraph"; content: string }
  | { type: "unordered-list"; items: { content: string; indent: number }[] }
  | { type: "ordered-list"; items: { content: string; indent: number }[] }
  | { type: "code"; language: string; content: string }
  | { type: "code-placeholder"; language: string };

function PendingReply() {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full bg-[#2a2d35] px-4 py-3">
      {[0, 1, 2].map((idx) => (
        <span
          key={`pending-dot-${idx}`}
          className="h-2 w-2 rounded-full bg-[#8f96a3] animate-pulse"
          style={{ animationDelay: `${idx * 160}ms` }}
        />
      ))}
    </div>
  );
}

function ChatPanelLoading() {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 px-6 py-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
          {Array.from({ length: 3 }).map((_, idx) => (
            <div
              key={`chat-loading-${idx}`}
              className="animate-pulse rounded-[24px] border border-[#2f3440] bg-[#1f232b] p-5"
            >
              <div className="h-4 w-24 rounded bg-[#31353f]" />
              <div className="mt-4 h-3 w-full rounded bg-[#2a2d35]" />
              <div className="mt-2 h-3 w-5/6 rounded bg-[#2a2d35]" />
              <div className="mt-2 h-3 w-2/3 rounded bg-[#2a2d35]" />
            </div>
          ))}
        </div>
      </div>
      <div className="shrink-0 border-t border-[#2a2e39] px-6 py-5">
        <div className="mx-auto w-full max-w-4xl animate-pulse rounded-[28px] border border-[#343946] bg-[#2a2d35] px-5 py-5">
          <div className="h-4 w-40 rounded bg-[#343946]" />
          <div className="mt-3 h-4 w-3/4 rounded bg-[#31353f]" />
        </div>
      </div>
    </div>
  );
}

function renderInlineRichText(text: string): React.ReactNode[] {
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean);
  return tokens.map((token, idx) => {
    if (token.startsWith("`") && token.endsWith("`")) {
      return (
        <code
          key={`inline-code-${idx}`}
          className="rounded-md bg-[#e8eaee] px-1.5 py-0.5 font-mono text-[0.95em] text-[#111318]"
        >
          {token.slice(1, -1)}
        </code>
      );
    }
    if (token.startsWith("**") && token.endsWith("**")) {
      return (
        <strong key={`inline-strong-${idx}`} className="font-semibold text-white">
          {token.slice(2, -2)}
        </strong>
      );
    }
    return <span key={`inline-text-${idx}`}>{token}</span>;
  });
}

function parseRichTextBlocks(content: string): RichTextBlock[] {
  const backtickMatches = content.match(/```/g);
  const hasUnclosedBlock =
    backtickMatches &&
    backtickMatches.length % 2 === 1 &&
    content.includes("```");
  if (hasUnclosedBlock) {
    const lastOpen = content.lastIndexOf("```");
    const beforeIncomplete = content.slice(0, lastOpen).trim();
    const afterOpen = content.slice(lastOpen);
    const langMatch = afterOpen.match(/^```(\w*)\s*\n?/);
    const language = (langMatch && langMatch[1]) || "python";
    const blocks = beforeIncomplete ? parseRichTextBlocksComplete(beforeIncomplete) : [];
    blocks.push({ type: "code-placeholder", language });
    return blocks;
  }
  return parseRichTextBlocksComplete(content);
}

function parseRichTextBlocksComplete(content: string): RichTextBlock[] {
  const segments = content
    .split(/(```[\s\S]*?```)/g)
    .map((segment) => segment.trim())
    .filter(Boolean);
  const blocks: RichTextBlock[] = [];

  for (const segment of segments) {
    if (segment.startsWith("```") && segment.endsWith("```")) {
      const body = segment.slice(3, -3).replace(/^\n+|\n+$/g, "");
      const firstNewline = body.indexOf("\n");
      const language = firstNewline >= 0 ? body.slice(0, firstNewline).trim() : "";
      const code = firstNewline >= 0 ? body.slice(firstNewline + 1) : body;
      blocks.push({
        type: "code",
        language,
        content: code,
      });
      continue;
    }

    const proseBlocks = segment
      .split(/\n\s*\n/g)
      .map((block) => block.trim())
      .filter(Boolean);

    for (const proseBlock of proseBlocks) {
      const lines = proseBlock.split("\n").map((line) => line.trimEnd()).filter(Boolean);
      if (lines.length === 0) continue;

      const unorderedItems = lines
        .map((line) => line.match(/^(\s*)[-*]\s+(.*)$/))
        .filter((match): match is RegExpMatchArray => Boolean(match));
      if (unorderedItems.length === lines.length) {
        blocks.push({
          type: "unordered-list",
          items: unorderedItems.map((match) => ({
            indent: Math.floor(match[1].length / 2),
            content: match[2].trim(),
          })),
        });
        continue;
      }

      const orderedItems = lines
        .map((line) => line.match(/^(\s*)\d+\.\s+(.*)$/))
        .filter((match): match is RegExpMatchArray => Boolean(match));
      if (orderedItems.length === lines.length) {
        blocks.push({
          type: "ordered-list",
          items: orderedItems.map((match) => ({
            indent: Math.floor(match[1].length / 2),
            content: match[2].trim(),
          })),
        });
        continue;
      }

      blocks.push({
        type: "paragraph",
        content: lines.join("\n"),
      });
    }
  }

  return blocks;
}

function CodePlaceholderBlock({ language }: { language: string }) {
  const { t } = useI18n();
  return (
    <div
      className="overflow-hidden rounded-[24px] border border-[#343946] bg-[#171a21] shadow-[0_14px_40px_rgba(0,0,0,0.2)]"
      role="status"
      aria-label={t.strategy.codeGenerating}
    >
      <div className="border-b border-[#2d313b] px-4 py-3 text-xs font-medium uppercase tracking-[0.18em] text-[#8f96a3]">
        {language || "Code"}
      </div>
      <div className="flex items-center gap-2 px-4 py-8">
        <div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-2 w-2 rounded-full bg-[#5f6472] animate-pulse"
              style={{ animationDelay: `${i * 160}ms` }}
            />
          ))}
        </div>
        <span className="text-sm text-[#8f96a3]">{t.strategy.codeGenerating}</span>
      </div>
    </div>
  );
}

function RichTextContent({ content }: { content: string }) {
  const blocks = parseRichTextBlocks(content);

  return (
    <div className="space-y-4 text-[15px] leading-7 text-[#ececf1]">
      {blocks.map((block, idx) => {
        if (block.type === "code-placeholder") {
          return (
            <CodePlaceholderBlock key={`rich-code-placeholder-${idx}`} language={block.language} />
          );
        }

        if (block.type === "code") {
          return (
            <div
              key={`rich-code-${idx}`}
              className="overflow-hidden rounded-[24px] border border-[#343946] bg-[#171a21] shadow-[0_14px_40px_rgba(0,0,0,0.2)]"
            >
              <div className="border-b border-[#2d313b] px-4 py-3 text-xs font-medium uppercase tracking-[0.18em] text-[#8f96a3]">
                {block.language || "Code"}
              </div>
              <pre className="scrollbar-hover max-h-[560px] overflow-auto px-4 py-4 font-mono text-xs leading-6 text-[#ececf1]">
                {block.content}
              </pre>
            </div>
          );
        }

        if (block.type === "unordered-list" || block.type === "ordered-list") {
          const ListTag = block.type === "ordered-list" ? "ol" : "ul";
          return (
            <ListTag
              key={`rich-list-${idx}`}
              className={`space-y-2 pl-6 ${
                block.type === "ordered-list" ? "list-decimal" : "list-disc"
              }`}
            >
              {block.items.map((item, itemIdx) => (
                <li
                  key={`rich-list-item-${idx}-${itemIdx}`}
                  className="marker:text-[#b9bec8]"
                  style={item.indent > 0 ? { marginLeft: `${item.indent * 16}px` } : undefined}
                >
                  {renderInlineRichText(item.content)}
                </li>
              ))}
            </ListTag>
          );
        }

        return (
          <p key={`rich-paragraph-${idx}`} className="whitespace-pre-wrap">
            {block.content.split("\n").map((line, lineIdx) => (
              <span key={`rich-line-${idx}-${lineIdx}`}>
                {lineIdx > 0 ? <br /> : null}
                {renderInlineRichText(line)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

function PromptComposer({
  centered = false,
  disabled = false,
  isSending,
  onChange,
  onCompositionEnd,
  onCompositionStart,
  onKeyDown,
  onSubmit,
  placeholder,
  prompt,
}: PromptComposerProps) {
  const busy = disabled || isSending;

  return (
    <div className={`w-full ${centered ? "max-w-3xl" : "max-w-4xl"}`}>
      <form
        className={`rounded-[28px] border border-[#343946] bg-[#2a2d35] shadow-[0_18px_50px_rgba(0,0,0,0.28)] ${
          centered ? "px-5 py-5" : "px-4 py-3"
        }`}
        onSubmit={onSubmit}
      >
        <div className="flex items-end gap-3">
          <textarea
            className={`flex-1 resize-none bg-transparent text-[15px] leading-7 text-[#ececf1] placeholder:text-[#8f96a3] focus:outline-none ${
              centered ? "min-h-[160px] px-1 py-1" : "min-h-[28px] max-h-[220px] px-1 py-2"
            }`}
            disabled={busy}
            onChange={(e) => onChange(e.target.value)}
            onCompositionStart={onCompositionStart}
            onCompositionEnd={onCompositionEnd}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            rows={centered ? 7 : 1}
            value={prompt}
          />
          <button
            aria-label="Send message"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#f4f4f4] text-[#111318] transition hover:bg-white disabled:cursor-not-allowed disabled:bg-[#3b404c] disabled:text-[#7b8393]"
            disabled={!prompt.trim() || busy}
            type="submit"
          >
            <svg
              aria-hidden="true"
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 16 16"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M8 13V3M8 3L4.5 6.5M8 3l3.5 3.5"
                stroke="currentColor"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="1.6"
              />
            </svg>
          </button>
        </div>
      </form>
      <p className={`mt-3 text-xs text-[#8f96a3] ${centered ? "text-center" : ""}`}>
        Note: execution settings in the Backtest/Live forms override values mentioned in this
        prompt.
      </p>
    </div>
  );
}

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
const LOADED_STRATEGY_SUMMARY_PROMPT =
  "Briefly explain this strategy in plain English in 5 bullets: overview, entry logic, exit logic, risk management, and practical cautions. Keep it concise.";
const buildGeneratedStrategySummaryPrompt = (userRequest: string) =>
  [
    `User request: ${userRequest}`,
    "Explain the generated strategy in the same language as the user's request.",
    "Write like a normal assistant reply, not a report.",
    "Structure: short intro paragraph, then concise bullets for entry, exit, risk management, and cautions.",
    "Use backticks for parameter or indicator names when useful.",
    "Do not include code fences or repeat the full code.",
  ].join("\n");

export default function StrategiesPage() {
  const { t } = useI18n();
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
  const [isLoadingStrategy, setIsLoadingStrategy] = useState(false);
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
  const sessionListScrollRef = useRef<HTMLDivElement | null>(null);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const emptyChatScrollRef = useRef<HTMLDivElement | null>(null);
  const workspaceGutterRef = useRef<HTMLDivElement | null>(null);
  const workspaceTextAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const workspaceResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const skipSessionSyncRef = useRef(false);
  const chatSessionsRef = useRef<ChatSessionRecord[]>([]);
  const shouldAutoScrollRef = useRef(true);
  const AUTO_SCROLL_THRESHOLD = 80;

  const routeWheelToScrollTarget = (event: React.WheelEvent, target: HTMLElement | null) => {
    if (!target) return;
    if (target.scrollHeight <= target.clientHeight) return;
    event.preventDefault();
    target.scrollTop += event.deltaY;
  };

  useEffect(() => {
    listStrategies()
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    let cancelled = false;

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
        const empty = createEmptySession();
        setChatSessions([empty]);
        setActiveSessionId(empty.id);
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
    const el = chatScrollRef.current;
    if (!el) return;
    if (shouldAutoScrollRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [chatMessages]);

  const handleChatScroll = useCallback(() => {
    const el = chatScrollRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    shouldAutoScrollRef.current = distanceFromBottom <= AUTO_SCROLL_THRESHOLD;
  }, []);

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
    shouldAutoScrollRef.current = true;
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
      const contentChanged =
        current.messages !== chatMessages ||
        current.workspaceCode !== workspaceCode ||
        current.workspaceSummary !== workspaceSummary ||
        current.workspaceSourceMessageId !== workspaceSourceMessageId ||
        current.initialGeneratedCode !== initialGeneratedCode;
      const next: ChatSessionRecord = {
        ...current,
        title: deriveSessionTitle(chatMessages),
        updatedAt: contentChanged ? new Date().toISOString() : current.updatedAt,
        messages: chatMessages,
        workspaceCode,
        workspaceSummary,
        workspaceSourceMessageId,
        initialGeneratedCode,
      };
      const updatedSessions = [...prev];
      updatedSessions[idx] = next;
      return contentChanged ? sortSessionsByUpdated(updatedSessions) : updatedSessions;
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
                statusText: cursor < fullText.length ? t.strategy.typing : null,
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
    if (!trimmed || isSending || isLoadingStrategy) {
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

    const doGenerate = (
      messagesToSend?: { role: string; content: string }[],
    ) => {
      setChatMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, status: "streaming", statusText: t.strategy.codeGenerating }
            : m,
        ),
      );
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
                  statusText: t.strategy.codeGenerating,
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
              const resolvedCode = payload.code ?? "";
              const resolvedSummary = payload.summary ?? null;
              const resolvedIsPythonCode = looksLikePythonCode(resolvedCode);
              setChatMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last?.role !== "assistant" || last.id !== assistantId) return prev;
                return [
                  ...prev.slice(0, -1),
                  {
                    ...last,
                    content: resolvedCode || last.content,
                    summary: resolvedSummary,
                    backtest_ok: payload.backtest_ok ?? false,
                    repaired: payload.repaired ?? false,
                    repair_attempts: payload.repair_attempts ?? 0,
                    textOnly: !resolvedIsPythonCode,
                    status: null,
                    statusText: null,
                  },
                ];
              });
              if (resolvedCode && resolvedIsPythonCode) {
                setWorkspaceCode(resolvedCode);
                setWorkspaceSourceMessageId(assistantId);
                setWorkspaceSummary(resolvedSummary);
                setWorkspaceDirty(false);
                setInitialGeneratedCode((prev) => prev ?? resolvedCode ?? null);
                if (!resolvedSummary) {
                  void strategyChat(
                    resolvedCode,
                    null,
                    [{ role: "user", content: buildGeneratedStrategySummaryPrompt(trimmed) }],
                  )
                    .then((summaryRes) => {
                      setChatMessages((prev) =>
                        prev.map((message) =>
                          message.id === assistantId
                            ? { ...message, summary: summaryRes.content }
                            : message,
                        ),
                      );
                      setWorkspaceSummary(summaryRes.content);
                    })
                    .catch(() => {
                      // keep code-only rendering if summary generation fails
                    });
                }
              } else if (resolvedCode && !resolvedIsPythonCode) {
                setWorkspaceSummary(null);
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
      statusText: t.strategy.codeGenerating,
    };

    if (options?.forceChat && activeCode) {
      shouldAutoScrollRef.current = true;
      setChatMessages((prev) => [...prev, { ...assistantMessage, textOnly: true }]);
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
      return;
    }

    shouldAutoScrollRef.current = true;
    setChatMessages((prev) => [...prev, assistantMessage]);
    try {
      const messagesToSend = buildMessagesForGeneration(
        nextMessages,
        activeCode || null,
        t.strategy.previousCodeContext,
      );
      await doGenerate(messagesToSend);
    } catch (e) {
      setChatError(String(e));
      setChatMessages((prev) => prev.filter((m) => m.id !== assistantId));
      setIsSending(false);
    }
  };

  const handleSubmit = async (event?: React.FormEvent<HTMLFormElement>) => {
    event?.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed || isSending || isLoadingStrategy) {
      return;
    }
    setPrompt("");
    await submitPrompt(trimmed);
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
      if (!next || isSending || isLoadingStrategy) return;
      setPrompt("");
      void submitPrompt(next);
    }
  };

  const handleClear = () => {
    if (isSending || isLoadingStrategy) return;
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
    if (isSending || isLoadingStrategy) return;
    const nextSession = createEmptySession();
    setChatSessions((prev) => [nextSession, ...prev]);
    setActiveSessionId(nextSession.id);
  };

  const handleSelectSession = (sessionId: string) => {
    if (sessionId === activeSessionId || isSending || isLoadingStrategy) return;
    setActiveSessionId(sessionId);
  };

  const handleDeleteSession = (sessionId: string) => {
    if (isSending || isLoadingStrategy) return;
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

  const handleOpenStrategyInWorkspace = async (path: string) => {
    if (isLoadingStrategy || isSending) return;
    setChatError(null);
    setIsLoadingStrategy(true);
    setActiveTab("chat");

    try {
      const loaded = await getStrategyContent(path);
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

      const strategyLabel = strategyNameFromPath(loaded.path || path);
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
      setChatError(String(e));
    } finally {
      setIsLoadingStrategy(false);
    }
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

  const latestAssistantCodeId =
    [...chatMessages]
      .reverse()
      .find((m) => m.role === "assistant" && !m.textOnly && Boolean(m.content) && looksLikePythonCode(m.content))?.id ?? null;
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
  const chatBusy = isSending || isLoadingStrategy;

  return (
    <main className="flex h-full min-h-0 w-full flex-1 flex-col overflow-hidden px-4 py-3">
      <div className="flex gap-1 border-b border-[#2a2e39]">
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
      <section className="relative mt-0 flex min-h-0 flex-1 flex-col overflow-hidden rounded-b-[24px] border border-t-0 border-[#2f3440] bg-[#21242b]">
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <aside
            className="hidden min-h-0 w-72 shrink-0 overflow-hidden border-r border-[#2f3440] bg-[#1a1d23] md:flex md:flex-col"
            onWheel={(event) => routeWheelToScrollTarget(event, sessionListScrollRef.current)}
          >
            <div className="border-b border-[#2f3440] px-3 py-3">
              <button
                type="button"
                className="w-full rounded-xl border border-[#343946] bg-[#2a2d35] px-3 py-2 text-sm text-[#ececf1] transition hover:border-[#505765] hover:bg-[#31353f] disabled:opacity-50"
                onClick={handleNewChatSession}
                disabled={chatBusy || !sessionsReady}
              >
                + New chat
              </button>
            </div>
            <div className="border-b border-[#2f3440] px-3 py-2 text-xs text-[#8f96a3]">
              {activeSession ? `Current: ${activeSession.title}` : "No active chat"}
            </div>
            {sessionSyncError ? (
              <p className="border-b border-[#ef5350]/30 bg-[#2d1f1f]/40 px-3 py-2 text-[11px] text-[#ef9a9a]">
                {sessionSyncError}
              </p>
            ) : null}
            <div ref={sessionListScrollRef} className="scrollbar-hover min-h-0 flex-1 overflow-y-auto px-2 py-2">
              {!sessionsReady ? (
                <div className="space-y-2 px-1">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="animate-pulse rounded-2xl border border-[#2f3440] bg-[#1f232b] p-3">
                      <div className="h-3.5 w-3/4 rounded bg-[#2f3440]" />
                      <div className="mt-2 h-2.5 w-1/2 rounded bg-[#2a2d35]" />
                      <div className="mt-1.5 h-2.5 w-1/3 rounded bg-[#2a2d35]" />
                    </div>
                  ))}
                </div>
              ) : chatSessions.length === 0 ? (
                <p className="px-2 py-3 text-xs text-[#868993]">No chats yet.</p>
              ) : (
                <div className="space-y-1">
                  {chatSessions.map((session) => {
                    const isActive = session.id === activeSessionId;
                    return (
                      <div
                        key={session.id}
                        className={`rounded-2xl border p-2 ${
                          isActive
                            ? "border-[#4d5565] bg-[#2a2d35]"
                            : "border-[#2f3440] bg-[#1f232b]"
                        }`}
                      >
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => handleSelectSession(session.id)}
                          disabled={chatBusy}
                        >
                          <p className="truncate text-sm text-[#d1d4dc]">{session.title}</p>
                          <p className="mt-1 text-[11px] text-[#8f96a3]">
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
                            disabled={chatBusy}
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
          <div
            className="min-w-0 min-h-0 flex-1 flex flex-col overflow-hidden"
            onWheel={(event) =>
              routeWheelToScrollTarget(
                event,
                !sessionsReady
                  ? null
                  : chatMessages.length > 0
                    ? chatScrollRef.current
                    : emptyChatScrollRef.current,
              )
            }
          >
            {sessionsReady ? (
              <>
                <div className="flex items-center gap-2 border-b border-[#2f3440] px-4 py-2 md:hidden">
                  <select
                    className="min-w-0 flex-1 rounded-xl border border-[#343946] bg-[#1f232b] px-2 py-1.5 text-xs text-[#d1d4dc] focus:border-[#505765] focus:outline-none"
                    value={activeSessionId ?? ""}
                    onChange={(e) => handleSelectSession(e.target.value)}
                    disabled={chatBusy || !sessionsReady}
                  >
                    {chatSessions.map((session) => (
                      <option key={`mobile-session-${session.id}`} value={session.id}>
                        {session.title}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="shrink-0 rounded-xl border border-[#343946] bg-[#2a2d35] px-2 py-1.5 text-xs text-[#ececf1] transition hover:border-[#505765] hover:bg-[#31353f] disabled:opacity-50"
                    onClick={handleNewChatSession}
                    disabled={chatBusy || !sessionsReady}
                  >
                    New
                  </button>
                </div>
                {chatMessages.length > 0 ? (
              <>
                <div
                  ref={chatScrollRef}
                  className="scrollbar-hover min-h-0 flex-1 overflow-y-auto px-6 py-6"
                  onScroll={handleChatScroll}
                >
                  <div className="mx-auto flex w-full max-w-4xl flex-col gap-8">
                    <div className="flex justify-end">
                      <button
                        type="button"
                        className="rounded-full border border-[#343946] px-4 py-2 text-xs text-[#8f96a3] transition hover:border-[#505765] hover:text-[#ececf1] disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={chatBusy}
                        onClick={handleClear}
                      >
                        Clear chat
                      </button>
                    </div>
                    {chatError ? (
                      <p className="rounded-2xl border border-[#ef5350]/30 bg-[#351f24] px-4 py-3 text-sm text-[#ef9a9a]">
                        {chatError}
                      </p>
                    ) : null}
                    {chatMessages.map((message) => {
                      const isLatestAssistantCode = message.id === latestAssistantCodeId;
                      const shouldShowPending =
                        message.role === "assistant" && !message.content && !message.summary;
                      const hasPythonCode = looksLikePythonCode(message.content);

                      if (message.role === "user") {
                        return (
                          <div key={message.id} className="flex justify-end">
                            <div className="max-w-[85%] rounded-[28px] bg-[#2d3139] px-5 py-3 text-[15px] leading-7 text-[#ececf1] shadow-[0_10px_30px_rgba(0,0,0,0.18)]">
                              <p className="whitespace-pre-wrap">{message.content}</p>
                            </div>
                          </div>
                        );
                      }

                      return (
                        <div key={message.id} className="flex items-start gap-4">
                          <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#343946] text-[11px] font-semibold text-[#ececf1]">
                            AI
                          </div>
                          <div className="min-w-0 flex-1 space-y-4">
                            {message.textOnly ? (
                              message.content ? (
                                <RichTextContent content={message.content} />
                              ) : shouldShowPending ? (
                                <PendingReply />
                              ) : null
                            ) : (
                              <>
                                {message.status === "streaming" || message.status === "thinking" ? (
                                  <CodePlaceholderBlock language="python" />
                                ) : message.content && hasPythonCode ? (
                                  <div className="overflow-hidden rounded-[24px] border border-[#343946] bg-[#171a21] shadow-[0_14px_40px_rgba(0,0,0,0.2)]">
                                    <div className="flex items-center justify-between gap-3 border-b border-[#2d313b] px-4 py-3">
                                      <span className="text-xs font-medium uppercase tracking-[0.18em] text-[#8f96a3]">
                                        Python
                                      </span>
                                      <div className="flex flex-wrap items-center gap-2">
                                        <button
                                          type="button"
                                          className="rounded-full border border-[#343946] px-3 py-1.5 text-xs text-[#c6cad3] transition hover:border-[#505765] hover:text-white"
                                          onClick={() => void handleCopy(message.content, message.id)}
                                        >
                                          {copiedId === message.id ? "Copied" : "Copy"}
                                        </button>
                                        {!message.path && isLatestAssistantCode && message.status == null ? (
                                          <button
                                            type="button"
                                            className="rounded-full border border-[#2962ff]/70 px-3 py-1.5 text-xs text-[#8fa8ff] transition hover:bg-[#1f3367] hover:text-white disabled:opacity-50"
                                            onClick={() => void handleSaveClick(message.id, message.content)}
                                            disabled={savingId !== null}
                                          >
                                            Save strategy
                                          </button>
                                        ) : null}
                                      </div>
                                    </div>
                                    <pre className="scrollbar-hover max-h-[560px] overflow-auto px-4 py-4 font-mono text-xs leading-6 text-[#ececf1]">
                                      {message.content}
                                    </pre>
                                  </div>
                                ) : message.content ? (
                                  <RichTextContent content={message.content} />
                                ) : shouldShowPending ? (
                                  <PendingReply />
                                ) : null}

                                {message.summary && hasPythonCode ? (
                                  <div className="space-y-3">
                                    <RichTextContent content={message.summary} />
                                  </div>
                                ) : null}

                                {(message.backtest_ok || message.repaired || message.path || message.model) ? (
                                  <div className="flex flex-wrap items-center gap-2 text-xs text-[#8f96a3]">
                                    {message.backtest_ok ? (
                                      <span className="rounded-full bg-[#183127] px-3 py-1 text-[#8ad0a4]">
                                        Backtest passed
                                      </span>
                                    ) : null}
                                    {message.repaired ? (
                                      <span className="rounded-full bg-[#3b2a17] px-3 py-1 text-[#f4bf75]">
                                        Auto-fix applied ({message.repair_attempts ?? 0})
                                      </span>
                                    ) : null}
                                    {message.path ? <span>Saved to Strategy Library</span> : null}
                                    {message.model ? <span>Model: {message.model}</span> : null}
                                  </div>
                                ) : null}
                              </>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
                <div className="flex-shrink-0 border-t border-[#2a2e39] px-6 py-5">
                  <div className="mx-auto flex w-full max-w-4xl justify-center">
                    <PromptComposer
                      disabled={chatBusy}
                      isSending={isSending}
                      onChange={setPrompt}
                      onCompositionEnd={() => setIsComposingPrompt(false)}
                      onCompositionStart={() => setIsComposingPrompt(true)}
                      onKeyDown={handleKeyDown}
                      onSubmit={handleSubmit}
                      placeholder="Message the strategy builder..."
                      prompt={prompt}
                    />
                  </div>
                </div>
              </>
            ) : (
              <div
                ref={emptyChatScrollRef}
                className="scrollbar-hover flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-12"
              >
                {chatError ? (
                  <p className="mb-6 w-full max-w-3xl rounded-2xl border border-[#ef5350]/30 bg-[#351f24] px-4 py-3 text-sm text-[#ef9a9a]">
                    {chatError}
                  </p>
                ) : null}
                <div className="max-w-3xl text-center">
                  <h2 className="text-[34px] font-medium tracking-[-0.03em] text-[#ececf1]">
                    What strategy do you want to build?
                  </h2>
                  <p className="mt-4 text-sm leading-7 text-[#8f96a3]">
                    Describe entries, exits, timeframe, and risk rules in plain language. The
                    assistant will turn it into runnable Python strategy code.
                  </p>
                </div>
                {isLoadingStrategy ? (
                  <div className="mt-8">
                    <PendingReply />
                  </div>
                ) : null}
                <div className="mt-10 flex w-full justify-center">
                  <PromptComposer
                    centered
                    disabled={chatBusy}
                    isSending={isSending}
                    onChange={setPrompt}
                    onCompositionEnd={() => setIsComposingPrompt(false)}
                    onCompositionStart={() => setIsComposingPrompt(true)}
                    onKeyDown={handleKeyDown}
                    onSubmit={handleSubmit}
                    placeholder="Describe your strategy in plain language. e.g. Buy when RSI crosses above 30, sell at 70, 1h candles."
                    prompt={prompt}
                  />
                </div>
              </div>
                )}
              </>
            ) : (
              <ChatPanelLoading />
            )}
          </div>

          {workspaceOpen ? (
            <div
              className="hidden w-1 shrink-0 cursor-col-resize bg-[#2a2e39] transition hover:bg-[#2962ff] lg:block"
              onMouseDown={handleWorkspaceResizeStart}
            />
          ) : null}
          <aside
            className={`relative hidden min-h-0 shrink-0 border-l border-[#2a2e39] bg-[#151924] lg:flex lg:flex-col ${
              workspaceOpen ? "" : "overflow-hidden border-l-0"
            }`}
            style={{ width: workspaceOpen ? workspaceWidth : 0 }}
            onWheel={(event) => {
              if (!workspaceCode.trim()) return;
              routeWheelToScrollTarget(event, workspaceTextAreaRef.current);
              if (workspaceGutterRef.current && workspaceTextAreaRef.current) {
                workspaceGutterRef.current.scrollTop = workspaceTextAreaRef.current.scrollTop;
              }
            }}
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
                        className="scrollbar-hover w-14 overflow-y-auto border-r border-[#2a2e39] bg-[#131722] py-3 text-right font-mono text-xs leading-6 text-[#5f6472]"
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
                        className="scrollbar-hover h-full flex-1 resize-none bg-transparent px-3 py-3 font-mono text-xs leading-6 text-[#d1d4dc] focus:outline-none"
                        spellCheck={false}
                        value={workspaceCode}
                        onChange={(e) => handleWorkspaceChange(e.target.value)}
                        onScroll={handleWorkspaceScroll}
                      />
                    </div>
                  ) : (
                    <div className="flex h-full items-center justify-center px-6 text-center text-sm text-[#868993]">
                      {t.strategy.codeGenHint}
                    </div>
                  )}
                </div>

                <div className="shrink-0 border-t border-[#2a2e39] px-3 py-2 text-xs">
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
                      <div className="scrollbar-hover mt-2 max-h-48 overflow-auto rounded border border-[#2a2e39] bg-[#0d111a] font-mono text-[11px] leading-5">
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
                role="button"
                tabIndex={0}
                className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-[#2a2e39] bg-[#131722] px-4 py-3 transition-colors hover:border-[#2962ff] hover:bg-[#252936]"
                onClick={() => void handleOpenStrategyInWorkspace(s.path)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    void handleOpenStrategyInWorkspace(s.path);
                  }
                }}
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-[#d1d4dc]">{strategyNameFromPath(s.path)}</div>
                  <div className="truncate text-xs text-[#868993]">Click to edit in workspace</div>
                </div>
                <button
                  type="button"
                  className="shrink-0 rounded border border-[#ef5350]/50 px-3 py-1.5 text-xs text-[#ef5350] transition hover:border-[#ef5350] hover:bg-[#ef5350]/10"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeleteClick(s.path);
                  }}
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
