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

export function getSpotKimpPctForMode(
  item: KimpScreenerItem,
  mode: KimpRateMode,
): number | null {
  if (mode === "bank") {
    return item.spot_bank_kimp_pct ?? null;
  }
  return item.spot_kimp_pct ?? null;
}
