import type { StrategyParamFieldSpec } from "@/lib/types";

export type DiffLine = {
  type: "context" | "add" | "remove";
  leftLineNo: number | null;
  rightLineNo: number | null;
  text: string;
};

export type ChatMessage = {
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

export type ChatSessionRecord = {
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

export type PromptComposerProps = {
  busyHint?: string;
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

export type RichTextBlock =
  | { type: "paragraph"; content: string }
  | { type: "unordered-list"; items: { content: string; indent: number }[] }
  | { type: "ordered-list"; items: { content: string; indent: number }[] }
  | { type: "code"; language: string; content: string }
  | { type: "code-placeholder"; language: string };

export type TabId = "chat" | "list";
export type WorkspaceSideTab = "params" | "code" | "backtest";

export const LOADED_STRATEGY_SUMMARY_PROMPT =
  "Briefly explain this strategy in plain English in 5 bullets: overview, entry logic, exit logic, risk management, and practical cautions. Keep it concise.";

export const buildGeneratedStrategySummaryPrompt = (userRequest: string) =>
  [
    `User request: ${userRequest}`,
    "Explain the generated strategy in the same language as the user's request.",
    "Write like a normal assistant reply, not a report.",
    "Structure: short intro paragraph, then concise bullets for entry, exit, risk management, and cautions.",
    "Use backticks for parameter or indicator names when useful.",
    "Do not include code fences or repeat the full code.",
  ].join("\n");

export const buildModificationSummaryPrompt = (userRequest: string) =>
  [
    `User's modification request: ${userRequest}`,
    "Summarize what was changed in the strategy, in the same language as the user's request.",
    "Write like a normal assistant reply — not a report.",
    "Focus on: what was modified, why, and any side-effects.",
    "Use backticks for parameter or indicator names when useful.",
    "Do not include code fences or repeat the full code.",
  ].join("\n");

/**
 * Strip trailing LLM refusal phrases that Azure OpenAI content filters
 * may inject mid-stream (e.g. "I'm sorry, but I cannot assist with that request.").
 */
export function stripLlmRefusal(text: string): string {
  // Common refusal patterns that can appear mid-stream from Azure OpenAI content filters
  const refusalPatterns = [
    /I'?m sorry,?\s*but I cannot assist with that request\.?\s*$/i,
    /I'?m sorry,?\s*but I can'?t assist with that\.?\s*$/i,
    /I cannot assist with that request\.?\s*$/i,
    /I can'?t help with that request\.?\s*$/i,
    /I'?m not able to (?:help|assist) with (?:that|this)\.?\s*$/i,
    /Sorry,?\s*I can'?t (?:help|assist) with that\.?\s*$/i,
    /This content may violate our (?:usage|content) polic(?:y|ies)\.?\s*$/i,
  ];
  let result = text;
  for (const pattern of refusalPatterns) {
    result = result.replace(pattern, "");
  }
  return result.trimEnd();
}

export function looksLikePythonCode(content: string): boolean {
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

export function getLastCodeAndSummary(messages: ChatMessage[]): {
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

export function toApiMessages(messages: ChatMessage[]): { role: string; content: string }[] {
  // Find the last assistant message containing strategy code.
  let lastCodeIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "assistant" && !m.textOnly && m.summary == null && looksLikePythonCode(m.content)) {
      lastCodeIdx = i;
      break;
    }
  }

  return messages.map((m, idx) => {
    // Assistant messages with summary or textOnly: use summary.
    if (m.role === "assistant" && (m.summary != null || m.textOnly)) {
      return { role: m.role, content: m.summary ?? m.content };
    }
    // Older assistant code messages: compress to placeholder.
    if (
      idx !== lastCodeIdx &&
      m.role === "assistant" &&
      looksLikePythonCode(m.content)
    ) {
      return { role: m.role, content: "[이전 전략 코드 — 워크스페이스에 반영됨]" };
    }
    return { role: m.role, content: m.content };
  });
}

export function buildMessagesForGeneration(
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

export function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

export function buildCodeDiffLines(beforeCode: string, afterCode: string): DiffLine[] {
  const beforeLines = beforeCode.split("\n");
  const afterLines = afterCode.split("\n");
  const n = beforeLines.length;
  const m = afterLines.length;

  if (n === 0 && m === 0) return [];

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

export const createId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;

export const isPresent = <T,>(value: T | null | undefined): value is T =>
  value !== null && value !== undefined;

export function toOptionalString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value;
}

export function sanitizeChatMessage(value: unknown): ChatMessage | null {
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

export function sanitizeChatSession(value: unknown): ChatSessionRecord | null {
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

export function sortSessionsByUpdated(sessions: ChatSessionRecord[]): ChatSessionRecord[] {
  return [...sessions].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

export function deriveSessionTitle(messages: ChatMessage[]): string {
  const firstUserMessage = messages.find((message) => message.role === "user" && message.content.trim());
  if (!firstUserMessage) return "New chat";
  const normalized = firstUserMessage.content.replace(/\s+/g, " ").trim();
  if (normalized.length <= 36) return normalized;
  return `${normalized.slice(0, 36)}...`;
}

export function createEmptySession(): ChatSessionRecord {
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

export function formatSessionTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  return parsed.toLocaleString();
}

import type { StrategyChatSessionRecord } from "@/lib/types";

export function fromRemoteSessionRecord(remote: StrategyChatSessionRecord): ChatSessionRecord | null {
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

export function toRemoteSessionData(session: ChatSessionRecord): Record<string, unknown> {
  return {
    messages: session.messages,
    workspaceCode: session.workspaceCode,
    workspaceSummary: session.workspaceSummary,
    workspaceSourceMessageId: session.workspaceSourceMessageId,
    initialGeneratedCode: session.initialGeneratedCode,
  };
}

export function normalizeStrategyParamPayload(
  draft: Record<string, unknown>,
  schemaFields: Record<string, StrategyParamFieldSpec>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(schemaFields)) {
    const raw = draft[key];
    const t = String(schemaFields[key]?.type ?? "").toLowerCase();
    if (t === "boolean") {
      out[key] = Boolean(raw);
    } else if (t === "integer") {
      const n = Number(raw);
      out[key] = Number.isFinite(n) ? Math.round(n) : 0;
    } else if (t === "number") {
      const n = Number(raw);
      out[key] = Number.isFinite(n) ? n : 0;
    } else {
      out[key] = raw == null ? "" : String(raw);
    }
  }
  return out;
}
