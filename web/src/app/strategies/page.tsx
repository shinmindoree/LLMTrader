"use client";

import { useEffect, useState } from "react";

import { listStrategies } from "@/lib/api";
import type { StrategyInfo } from "@/lib/types";

export default function StrategiesPage() {
  const [items, setItems] = useState<StrategyInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listStrategies()
      .then(setItems)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-xl font-semibold text-[#d1d4dc]">Strategies</h1>
      {error ? (
        <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}
      {items.length === 0 && !error ? (
        <div className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
          No strategies found.
        </div>
      ) : (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((s) => (
            <div
              key={s.path}
              className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
            >
              <div className="font-medium text-[#d1d4dc]">{s.name}</div>
              <div className="mt-1 font-mono text-xs text-[#868993]">{s.path}</div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}

