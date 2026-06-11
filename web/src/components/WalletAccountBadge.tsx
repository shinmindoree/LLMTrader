import type { WalletAccount } from "@/lib/types";

export type WalletAccountEnv = "mainnet" | "testnet";

function walletBadgeClasses(wallet: WalletAccount | undefined, hasWalletId: boolean): string {
  if (!hasWalletId) {
    return "border-[#2a2e39] bg-[#131722] text-[#868993]";
  }
  if (!wallet) {
    return "border-[#efb74d]/30 bg-[#2d2718]/50 text-[#efb74d]";
  }
  if (wallet.role === "sub") {
    return "border-[#7c3aed]/40 bg-[#2a1f45] text-[#c4b5fd]";
  }
  return "border-[#2962ff]/40 bg-[#172554] text-[#93c5fd]";
}

function walletRoleLabel(wallet: WalletAccount): string {
  return wallet.role === "sub" ? "Sub" : "Master";
}

function walletTitle(wallet: WalletAccount | undefined, accountEnv: WalletAccountEnv): string {
  if (!wallet) return `거래 계정: wallet 정보를 불러오는 중 (${accountEnv})`;
  const parts = [
    `거래 계정: ${wallet.alias}`,
    walletRoleLabel(wallet),
    wallet.env,
    wallet.sub_account_email,
    wallet.api_key_masked ? `API ${wallet.api_key_masked}` : null,
  ].filter(Boolean);
  return parts.join(" · ");
}

export function WalletAccountBadge({
  wallet,
  walletAccountId,
  accountEnv,
  showPrefix = true,
}: {
  wallet: WalletAccount | undefined;
  walletAccountId: string | null | undefined;
  accountEnv: WalletAccountEnv;
  showPrefix?: boolean;
}) {
  const hasWalletId = Boolean(walletAccountId);
  const label = !hasWalletId
    ? "기본 Binance API 키"
    : wallet
      ? `${wallet.alias} · ${walletRoleLabel(wallet)}`
      : "계정 정보 로딩 중";
  const detail = wallet?.sub_account_email ?? wallet?.api_key_masked ?? accountEnv;

  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 rounded border px-2 py-1 text-[11px] ${walletBadgeClasses(wallet, hasWalletId)}`}
      title={hasWalletId ? walletTitle(wallet, accountEnv) : `거래 계정: 기본 Binance API 키 · ${accountEnv}`}
    >
      {showPrefix ? <span className="shrink-0 text-[10px] opacity-70">계정</span> : null}
      <span className="truncate font-medium">{label}</span>
      <span className="hidden max-w-[260px] truncate opacity-70 sm:inline">· {detail}</span>
    </span>
  );
}
