import type { KimpRateMode, KimpScreenerItem } from "@/lib/types";

export function getKimpPctForMode(
  item: KimpScreenerItem,
  mode: KimpRateMode,
): number | null {
  if (mode === "bank") {
    return item.bank_kimp_pct ?? null;
  }
  return item.kimp_pct;
}
