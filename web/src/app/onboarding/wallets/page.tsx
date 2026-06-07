import { redirect } from "next/navigation";

/**
 * Legacy onboarding wallets route. The flow has moved to
 * Settings → Sub account (a much simpler UX where the operator
 * creates sub-accounts on Binance and reconciles them into the app).
 * We keep the route here so old bookmarks land on the new page
 * instead of 404'ing.
 */
export default function LegacyOnboardingWalletsPage() {
  redirect("/settings?tab=sub");
}
