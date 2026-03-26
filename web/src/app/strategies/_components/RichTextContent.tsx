"use client";

import { useI18n } from "@/lib/i18n";
import type { RichTextBlock } from "../_lib/helpers";

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

export function CodePlaceholderBlock({ language, statusText }: { language: string; statusText?: string | null }) {
  const { t } = useI18n();
  const displayText = statusText || t.strategy.codeGenerating;
  return (
    <div
      className="overflow-hidden rounded-[24px] border border-[#343946] bg-[#171a21] shadow-[0_14px_40px_rgba(0,0,0,0.2)]"
      role="status"
      aria-label={displayText}
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
        <span className="text-sm text-[#8f96a3]">{displayText}</span>
      </div>
    </div>
  );
}

export function RichTextContent({ content }: { content: string }) {
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
