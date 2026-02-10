from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from binance.client import BinanceHTTPClient
from control.repo import upsert_account_snapshot

SNAPSHOT_KEY = "binance_futures"
DEFAULT_INTERVAL_SECONDS = 15


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _binance_mode(base_url: str) -> Literal["testnet", "mainnet", "custom"]:
    lowered = base_url.lower()
    if "testnet" in lowered:
        return "testnet"
    if "fapi.binance.com" in lowered:
        return "mainnet"
    return "custom"


async def _fetch_snapshot(
    api_key: str,
    api_secret: str,
    base_url: str,
) -> dict[str, Any]:
    mode = _binance_mode(base_url)

    client = BinanceHTTPClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
    )
    try:
        account = await client.fetch_account_info()

        btc_price: float | None = None
        try:
            btc_price = await client.fetch_ticker_price("BTCUSDT")
        except Exception:  # noqa: BLE001
            btc_price = None

        assets: list[dict[str, Any]] = []
        raw_assets = account.get("assets", [])
        if isinstance(raw_assets, list):
            for raw_asset in raw_assets:
                if not isinstance(raw_asset, dict):
                    continue
                asset_name = str(raw_asset.get("asset") or "").strip().upper()
                if not asset_name:
                    continue

                wallet_balance = _safe_float(raw_asset.get("walletBalance"))
                available_balance = _safe_float(raw_asset.get("availableBalance"))
                unrealized_profit = _safe_float(raw_asset.get("unrealizedProfit"))
                margin_balance = _safe_float(
                    raw_asset.get("marginBalance"),
                    wallet_balance + unrealized_profit,
                )

                if (
                    abs(wallet_balance) < 1e-12
                    and abs(available_balance) < 1e-12
                    and abs(unrealized_profit) < 1e-12
                    and abs(margin_balance) < 1e-12
                ):
                    continue

                assets.append({
                    "asset": asset_name,
                    "wallet_balance": wallet_balance,
                    "available_balance": available_balance,
                    "unrealized_profit": unrealized_profit,
                    "margin_balance": margin_balance,
                })
        assets.sort(key=lambda item: abs(item["margin_balance"]), reverse=True)

        positions: list[dict[str, Any]] = []
        raw_positions = account.get("positions", [])
        if isinstance(raw_positions, list):
            for raw_position in raw_positions:
                if not isinstance(raw_position, dict):
                    continue
                symbol = str(raw_position.get("symbol") or "").strip().upper()
                if not symbol:
                    continue

                position_amt = _safe_float(raw_position.get("positionAmt"))
                unrealized_pnl = _safe_float(raw_position.get("unrealizedProfit"))
                notional = _safe_float(raw_position.get("notional"))

                if abs(position_amt) < 1e-12 and abs(unrealized_pnl) < 1e-12 and abs(notional) < 1e-12:
                    continue

                entry_price = _safe_float(raw_position.get("entryPrice"))
                break_even_price = _safe_float(raw_position.get("breakEvenPrice"), entry_price)
                leverage = max(1, _safe_int(raw_position.get("leverage"), 1))

                positions.append({
                    "symbol": symbol,
                    "side": "LONG" if position_amt >= 0 else "SHORT",
                    "position_amt": position_amt,
                    "entry_price": entry_price,
                    "break_even_price": break_even_price,
                    "unrealized_pnl": unrealized_pnl,
                    "notional": notional,
                    "leverage": leverage,
                    "isolated": bool(raw_position.get("isolated", False)),
                })
        positions.sort(key=lambda item: abs(item["notional"]), reverse=True)

        total_wallet_balance = _safe_float(
            account.get("totalWalletBalance"),
            _safe_float(account.get("walletBalance")),
        )
        total_unrealized_profit = _safe_float(
            account.get("totalUnrealizedProfit"),
            sum(a["unrealized_profit"] for a in assets),
        )
        total_margin_balance = _safe_float(
            account.get("totalMarginBalance"),
            total_wallet_balance + total_unrealized_profit,
        )
        available_balance = _safe_float(account.get("availableBalance"))
        total_wallet_balance_btc = (
            (total_wallet_balance / btc_price)
            if (btc_price is not None and btc_price > 0)
            else None
        )
        can_trade_raw = account.get("canTrade")

        return {
            "configured": True,
            "connected": True,
            "mode": mode,
            "base_url": base_url,
            "total_wallet_balance": total_wallet_balance,
            "total_wallet_balance_btc": total_wallet_balance_btc,
            "total_unrealized_profit": total_unrealized_profit,
            "total_margin_balance": total_margin_balance,
            "available_balance": available_balance,
            "can_trade": bool(can_trade_raw) if can_trade_raw is not None else None,
            "update_time": datetime.now(timezone.utc).isoformat(),
            "assets": assets,
            "positions": positions,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "connected": False,
            "mode": mode,
            "base_url": base_url,
            "error": str(exc)[:1000],
            "update_time": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await client.aclose()


async def run_account_snapshot_loop(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    api_key: str,
    api_secret: str,
    base_url: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            data = await _fetch_snapshot(api_key, api_secret, base_url)
            async with session_maker() as session:
                await upsert_account_snapshot(session, key=SNAPSHOT_KEY, data_json=data)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[runner] account snapshot error: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval_seconds)
