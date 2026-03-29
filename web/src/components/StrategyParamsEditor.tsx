"use client";

import { useEffect, useState } from "react";
import { extractStrategyParams, getStrategyContent } from "@/lib/api";
import type { StrategyParamFieldSpec, StrategyParamsExtractResponse } from "@/lib/types";

const GROUP_ORDER = [
  "진입 (Entry)",
  "청산 (Exit)",
  "지표 (Indicator)",
  "리스크 관리 (Risk)",
  "일반 (General)",
];
const GROUP_ICONS: Record<string, string> = {
  "진입 (Entry)": "▶",
  "청산 (Exit)": "◼",
  "지표 (Indicator)": "📊",
  "리스크 관리 (Risk)": "🛡",
  "일반 (General)": "⚙",
};

type Props = {
  /** Strategy path — fetch code + extract params automatically */
  strategyPath?: string;
  /** Or provide code directly (overrides strategyPath) */
  code?: string;
  /** Current parameter overrides */
  values: Record<string, unknown>;
  /** Called when user modifies a parameter value */
  onChange: (values: Record<string, unknown>) => void;
  /** Disable all inputs */
  disabled?: boolean;
};

export default function StrategyParamsEditor({
  strategyPath,
  code: codeProp,
  values,
  onChange,
  disabled = false,
}: Props) {
  const [schema, setSchema] = useState<StrategyParamsExtractResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        let source = codeProp?.trim() ?? "";
        if (!source && strategyPath) {
          const content = await getStrategyContent(strategyPath);
          source = content?.code ?? "";
        }
        if (!source) {
          setSchema(null);
          return;
        }
        const result = await extractStrategyParams(source);
        if (cancelled) return;
        setSchema(result);

        // Initialize values from defaults if empty
        if (result.supported && Object.keys(values).length === 0) {
          onChange({ ...result.values });
        }
      } catch {
        if (!cancelled) setError("파라미터를 불러오지 못했습니다.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyPath, codeProp]);

  if (loading) {
    return <p className="px-3 py-2 text-xs text-[#8fa8ff]">파라미터 불러오는 중...</p>;
  }

  if (error) {
    return <p className="px-3 py-2 text-xs text-[#ef9a9a]">{error}</p>;
  }

  if (!schema?.supported) {
    return (
      <p className="px-3 py-2 text-xs text-[#868993]">
        이 전략은 조정 가능한 파라미터가 없습니다.
      </p>
    );
  }

  const fields = schema.schema_fields;
  const groups: Record<string, string[]> = {};
  for (const key of Object.keys(fields)) {
    const g = fields[key]?.group || "일반 (General)";
    (groups[g] ??= []).push(key);
  }
  const sortedGroups = Object.keys(groups).sort(
    (a, b) =>
      (GROUP_ORDER.indexOf(a) === -1 ? 99 : GROUP_ORDER.indexOf(a)) -
      (GROUP_ORDER.indexOf(b) === -1 ? 99 : GROUP_ORDER.indexOf(b)),
  );

  return (
    <div className={`flex flex-col gap-3${disabled ? " pointer-events-none opacity-50" : ""}`}>
      {sortedGroups.map((groupName) => (
        <fieldset
          key={groupName}
          className="rounded border border-[#2a2e39] px-3 pb-3 pt-2"
        >
          <legend className="px-1 text-[11px] font-semibold tracking-wide text-[#8fa8ff]">
            {GROUP_ICONS[groupName] ?? "•"} {groupName}
          </legend>
          <div className="flex flex-col gap-3">
            {groups[groupName].map((key) => {
              const spec: StrategyParamFieldSpec = fields[key] ?? ({} as StrategyParamFieldSpec);
              const label =
                typeof spec.label === "string" && spec.label.trim() ? spec.label : key;
              const description =
                typeof spec.description === "string" && spec.description.trim()
                  ? spec.description
                  : null;
              const tRaw = String(spec.type ?? "").toLowerCase();
              const minV = typeof spec.min === "number" ? spec.min : undefined;
              const maxV = typeof spec.max === "number" ? spec.max : undefined;
              const draftVal = values[key] ?? schema.values[key];

              if (tRaw === "boolean") {
                return (
                  <div key={key} className="flex flex-col gap-0.5">
                    <label className="flex cursor-pointer items-center gap-2 text-sm text-[#d1d4dc]">
                      <input
                        type="checkbox"
                        className="h-4 w-4 rounded border border-[#2a2e39] bg-[#131722]"
                        checked={Boolean(draftVal)}
                        onChange={(e) =>
                          onChange({ ...values, [key]: e.target.checked })
                        }
                      />
                      <span>{label}</span>
                    </label>
                    {description && (
                      <p className="pl-6 text-[11px] leading-snug text-[#6b7383]">
                        {description}
                      </p>
                    )}
                  </div>
                );
              }

              if (tRaw === "integer" || tRaw === "number") {
                const num =
                  typeof draftVal === "number"
                    ? draftVal
                    : Number.parseFloat(String(draftVal ?? ""));
                const display = Number.isFinite(num) ? num : "";
                return (
                  <div key={key} className="flex flex-col gap-1">
                    <label
                      className="text-[11px] font-medium text-[#9aa0ad]"
                      htmlFor={`sp-${key}`}
                    >
                      {label}
                    </label>
                    {description && (
                      <p className="text-[11px] leading-snug text-[#6b7383]">{description}</p>
                    )}
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                      <input
                        id={`sp-${key}`}
                        type="number"
                        className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none sm:max-w-[140px]"
                        min={minV}
                        max={maxV}
                        step={tRaw === "integer" ? 1 : "any"}
                        value={display === "" ? "" : display}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === "") {
                            onChange({ ...values, [key]: "" });
                            return;
                          }
                          const parsed =
                            tRaw === "integer"
                              ? Number.parseInt(v, 10)
                              : Number.parseFloat(v);
                          onChange({
                            ...values,
                            [key]: Number.isFinite(parsed) ? parsed : v,
                          });
                        }}
                      />
                      {minV !== undefined && maxV !== undefined && (
                        <input
                          type="range"
                          className="h-2 w-full accent-[#2962ff]"
                          min={minV}
                          max={maxV}
                          step={tRaw === "integer" ? 1 : (maxV - minV) / 200}
                          value={
                            Number.isFinite(num)
                              ? Math.min(maxV, Math.max(minV, num))
                              : minV
                          }
                          onChange={(e) => {
                            const parsed = Number.parseFloat(e.target.value);
                            onChange({
                              ...values,
                              [key]:
                                tRaw === "integer" ? Math.round(parsed) : parsed,
                            });
                          }}
                        />
                      )}
                    </div>
                  </div>
                );
              }

              // String fallback
              return (
                <div key={key} className="flex flex-col gap-1">
                  <label
                    className="text-[11px] font-medium text-[#9aa0ad]"
                    htmlFor={`sp-${key}`}
                  >
                    {label}
                  </label>
                  {description && (
                    <p className="text-[11px] leading-snug text-[#6b7383]">{description}</p>
                  )}
                  <input
                    id={`sp-${key}`}
                    type="text"
                    className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                    value={draftVal == null ? "" : String(draftVal)}
                    onChange={(e) =>
                      onChange({ ...values, [key]: e.target.value })
                    }
                  />
                </div>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
