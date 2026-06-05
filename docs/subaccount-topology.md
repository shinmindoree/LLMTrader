# Sub-account Topology — Operator Guide

This document explains how LLMTrader isolates multiple Binance trading
strategies inside a single user account by mapping each strategy onto a
dedicated Binance **virtual sub-account**, and how the platform moves
funds between them automatically.

> Status: implemented in main as of the *"Absorbing auto_sweep into
> CapitalRouter"* checkpoint. See the session plan
> (`plan.md::Sub-Account 기반 멀티전략 자금 격리 통합 설계`) for the
> full design rationale.

---

## 1. Why sub-accounts

Running multiple strategies (directional alpha, basis arbitrage,
options/derivatives, Simple Earn) on the **same** Binance account leads
to three concrete failure modes:

1. **Margin contamination.** One leg's losses raise the *whole*
   account's maintenance margin, possibly liquidating an unrelated
   leg that was healthy on its own.
2. **Position netting.** Opposite-side legs of a basis/funding
   arbitrage net out at the exchange and never get treated as a
   hedged pair. Risk reporting and PnL attribution silently break.
3. **Concurrent rebalances.** Two engines moving USDT between
   Spot↔Futures fight each other inside the same wallet.

Binance's *virtual sub-accounts* give us **physical** isolation: each
sub has its own margin, its own positions, its own Futures account, and
its own API keys. Universal Transfer (UID weight 360) is the only way
funds cross between them, and only the **master** key can initiate
those transfers.

---

## 2. Topology

```
UserProfile (user_id)
└─ Master account (env=mainnet)
   ├─ master API key  — read · enable internal transfer · enable universal transfer
   ├─ Spot wallet     — holds the idle / Earn-bound float
   └─ Sub-accounts
      ├─ directional   (USDT-M Futures only)
      ├─ arbitrage     (Spot + USDT-M Futures)
      ├─ derivatives   (Futures + Options)
      └─ earn          (Spot only — Simple Earn parking)
```

* The **master account never trades.** It only holds spot float and
  routes USDT to subs.
* Each **sub trades a single strategy family.** Same family, multiple
  symbols (e.g. directional alpha on BTC, ETH, SOL) is fine.
* Simple Earn lives on the master only — Binance Earn isn't generally
  available on subs.
* The `earn` sub is optional. It exists so users who want a separate
  fund-flow audit trail for the Earn float can opt in.

---

## 3. Components

| Layer            | Module                              | Responsibility |
| ---------------- | ----------------------------------- | -------------- |
| DB schema        | `control/models.py`, alembic        | `wallet_accounts`, `strategy_allocations`, `wallet_transfers`, `Job.wallet_account_id` |
| Binance SDK      | `binance/subaccount_client.py`      | sapi sub-account REST (`create`, `enable_futures`, `enable_options`, `ip_restriction`, `universal_transfer`, asset/futures queries) |
| Client factory   | `binance/client_factory.py`         | LRU-cached `BinanceHTTPClient` / `BinanceEarnClient` / `BinanceSubAccountClient` per `wallet_account_id` |
| Fund-flow engine | `live/capital_router.py`            | Periodic master↔sub rebalancing + Earn subscribe/redeem; **absorbs** the old `auto_sweep_engine` |
| Pre-trade gate   | `live/allocator.py`                 | App-level capital reservation: reject / clamp / grant a notional against `strategy_allocations` |
| HTTP API         | `src/api/wallets.py`                | CRUD on wallet accounts, allocations, transfers |
| Web UI           | `web/src/app/onboarding/wallets/`   | 3-step wizard: master key → auto-create subs → enter sub trading keys |

---

## 4. Onboarding (one-time, ~5 min)

The wizard at **`/onboarding/wallets`** drives the full flow:

1. **Step 1 — Master key.**
   Register a *mainnet* Binance API key with only these permissions:
   - Read
   - Enable Internal Transfer
   - Enable Universal Transfer
   - **NO** trade / withdraw / margin permissions
   - IP whitelist enabled (the worker IP must be allowed)

2. **Step 2 — Auto-create subs.**
   The wizard calls `POST /api/me/wallets/subaccounts` once per
   template (`directional`, `arbitrage`, `derivatives`, `earn`),
   which in turn:
   - Calls Binance `POST /sapi/v1/sub-account/virtualSubAccount`
   - Calls `POST /sapi/v1/sub-account/futures/enable` (if needed)
   - Calls `POST /sapi/v1/sub-account/eoptions/enable` (if needed)
   - Inserts a `wallet_accounts` row with `status='key_missing'`

   Subs are *idempotent by alias*: re-running the wizard with the
   same alias is a no-op rather than a duplicate.

3. **Step 3 — Per-sub trading keys.**
   Binance does **not** let the master programmatically create trading
   keys for a sub (this is a Binance security policy, not a platform
   limitation). The user must:
   - Open
     https://www.binance.com/en/my/security/api-management
   - Switch to the sub account
   - Create an API key with **only** the permissions that strategy
     needs (e.g. Enable Futures, but not Enable Withdrawals)
   - Paste key + secret into the wizard

   On save, the backend calls
   `POST /sapi/v1/sub-account/subAccountApi/ipRestriction/ipList` to
   apply the IP whitelist automatically, then flips
   `status='active'`.

After Step 3, the sub is fully usable. Allocations can be set per-job
from the Jobs UI (or via `PUT /api/me/jobs/{job_id}/allocation`).

---

## 5. Capital Router — fund flow

`CapitalRouter.cycle()` runs every `auto_sweep_poll_interval_sec`
seconds (default 60). For each user with `auto_sweep_enabled=true`:

```
list_auto_sweep_enabled_users()
└─ process_user(user_id)
   ├─ if user has active sub wallets → _process_user_with_subs()
   │  ├─ for each sub: read Futures available balance via master
   │  │   sub-account API
   │  ├─ if balance < topup_threshold → transfer from master Spot
   │  ├─ if balance > buffer → transfer surplus back to master Spot
   │  └─ _handle_master_earn()
   │     ├─ if master Spot < required for topups → redeem Earn
   │     └─ if master Spot has subscribe-worthy surplus → subscribe Earn
   │
   └─ else → _process_user_legacy()        # single-account, identical to old auto_sweep
```

Every transfer goes through `CapitalRouter.transfer()`, which:

1. Generates a deterministic `client_tran_id` (≤ 32 alphanumeric chars).
2. Inserts a `wallet_transfers` row with `status='PENDING'`.
3. Calls `POST /sapi/v1/sub-account/universalTransfer`.
4. Updates the row to `SUCCEEDED` / `FAILED` with the Binance tran id.

`client_tran_id` is the **idempotency** key: Binance dedupes within its
own window, and we additionally short-circuit on a duplicate hit by
looking the row up via `get_wallet_transfer_by_client_id` before
calling the API.

### Snapshot compatibility

To keep the existing `/api/me/auto-sweep/status` endpoint working
unchanged, the router still writes its summary under the snapshot key
`auto_sweep:{user_id}` with the same payload shape, plus new
sub-aware fields (`subs[]`, `topology='sub-aware'|'legacy'`).

---

## 6. Capital Allocator — app-level gate

Sub-accounts give us *physical* fund isolation. The allocator gives an
*extra* logical gate that prevents one strategy from outgrowing the
budget the operator granted it.

* Source of truth: `strategy_allocations.{allocated_usdt, reserved_usdt}`.
* `reserve(job_id, notional)` → `OK` / `CLAMPED` / `REJECTED`.
* `release(job_id, notional)` — called on fill, cancel, position close.
* Negative-drift safe: `release` clamps `reserved_usdt` to `0.0` if it
  ever underflows.
* Per-job `asyncio.Lock` ensures `reserve` is atomic within one
  worker. Multi-worker deployments need a SQL-level guard
  (conditional UPDATE) — tracked as a follow-up.

### Operating modes

* `allow_clamp=False` (default) — over-budget reservations are
  rejected outright. Safer; produces clean RISK events.
* `allow_clamp=True` — partial reservations are granted up to the
  current free budget. Useful for strategies that downsize gracefully
  rather than skipping the trade entirely.

---

## 7. Common runbooks

### Adding a new sub mid-life

1. Open `/onboarding/wallets` → Step 2 → pick the missing template
   (e.g. `derivatives`). The wizard handles the rest.
2. Issue a trading key in Binance for the new sub.
3. Paste it into Step 3.
4. Create a new Job (or reassign an existing one) bound to the new
   sub via the Jobs UI / `PUT /api/me/jobs/{job_id}/allocation`.

### Rotating a sub trading key

1. Disable the existing key in Binance.
2. Open `/onboarding/wallets` → Step 3 → "키 교체" on that sub.
3. Paste the new key + secret. The IP whitelist is re-applied
   automatically.

### Topping up a sub manually

The Capital Router rebalances every minute, so this is rarely needed.
If you must:

```bash
# Use the universal transfer endpoint behind the auth header.
curl -X POST $API/api/me/wallet-transfers ...   # (planned helper)
```

For now: call `CapitalRouter.transfer()` from a one-shot async script,
or temporarily lower `sub_futures_min_buffer_usdt` so the next cycle
performs the top-up.

### Investigating a failed transfer

1. `GET /api/me/wallet-transfers?limit=50` — find rows with
   `status='failed'` and look at `error_message`.
2. Cross-reference `client_tran_id` against Binance's transfer
   history.
3. Common causes:
   - **IP not whitelisted** — re-run Step 3 to re-apply.
   - **Insufficient balance** — Earn redeem hadn't settled yet;
     the next cycle will retry.
   - **Sub account disabled by Binance risk** — manual unlock at
     Binance is required; the router will not retry until status
     changes.

### Disabling all routing temporarily

Toggle `UserProfile.auto_sweep_enabled` off. The router will skip the
user entirely without touching wallets.

---

## 8. Testing

* **Unit** — `test/unit/test_capital_router.py` and
  `test/unit/test_capital_allocator.py` cover the pure decision logic
  and concurrency contract. Run with:

  ```powershell
  .venv\Scripts\python.exe -m pytest test\unit -v
  ```

* **Integration (manual)** — Binance Spot/Futures testnets do not
  fully support the sub-account API. End-to-end validation is done
  against a real mainnet account with small amounts:
  1. Run the wizard.
  2. Place a tiny order on each sub.
  3. Drain a sub's Futures balance and confirm Capital Router
     tops it up within one cycle.
  4. Check `wallet_transfers` for matching `SUCCEEDED` rows.

---

## 9. Known limitations

* Sub-account *trading* keys cannot be created via API — manual step
  required (Binance policy).
* Simple Earn assumed to live only on the master account.
* Copy Trading is **out of scope** — Binance's Copy Trading API is
  not exposed via the standard REST surface.
* Multi-worker `CapitalAllocator` deployment needs a SQL-level
  reservation guard (planned).
* Universal Transfer is UID-weight 360 — bursts above ~10 transfers
  per minute will hit Binance's per-UID limit. The router caps at
  `max_transfers_per_cycle=5` by default.

---

## 10. Reference

* Plan: `plan.md` (session-state)
* Models: `src/control/models.py`
  (`WalletAccount`, `StrategyAllocation`, `WalletTransfer`)
* Repo CRUD: `src/control/repo.py` (lines ~1284–1660)
* Routes: `src/api/wallets.py`
* Engine: `src/live/capital_router.py`
* Gate: `src/live/allocator.py`
* Web wizard: `web/src/app/onboarding/wallets/page.tsx`
