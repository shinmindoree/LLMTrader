"""Unit tests for ``live.capital_router`` pure helpers.

We focus on the parts that don't need a real DB or Binance — the
``RoutingPolicy`` lookup behaviour and the ``_generate_client_tran_id``
idempotency key contract that Binance's universal-transfer endpoint
relies on.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from live.capital_router import (
    RoutingPolicy,
    _generate_client_tran_id,
)


class TestRoutingPolicy:
    def test_defaults_match_legacy_behaviour(self) -> None:
        pol = RoutingPolicy()
        assert pol.master_spot_buffer_usdt == 100.0
        assert pol.earn_min_subscribe_usdt == 50.0
        assert pol.max_transfers_per_cycle == 5
        assert pol.min_transfer_usdt == Decimal("1")
        # The per-alias maps are empty by default; lookups must fall back.
        assert pol.sub_futures_min_buffer_usdt == {}
        assert pol.sub_futures_topup_threshold_usdt == {}

    def test_buffer_for_uses_override_when_present(self) -> None:
        pol = RoutingPolicy(
            sub_futures_min_buffer_usdt={"directional": 250.0, "arbitrage": 80.0},
        )
        assert pol.buffer_for("directional") == 250.0
        assert pol.buffer_for("arbitrage") == 80.0

    def test_buffer_for_falls_back_to_default(self) -> None:
        pol = RoutingPolicy()
        assert pol.buffer_for("anything-unmapped") == 50.0
        assert pol.buffer_for("anything-unmapped", default=12.5) == 12.5

    def test_topup_threshold_for_uses_override_when_present(self) -> None:
        pol = RoutingPolicy(
            sub_futures_topup_threshold_usdt={"derivatives": 75.0},
        )
        assert pol.topup_threshold_for("derivatives") == 75.0

    def test_topup_threshold_falls_back_to_default(self) -> None:
        pol = RoutingPolicy()
        assert pol.topup_threshold_for("missing") == 30.0
        assert pol.topup_threshold_for("missing", default=7.0) == 7.0


class TestGenerateClientTranId:
    def test_length_within_binance_limit(self) -> None:
        # Binance caps clientTranId at 32 alphanumeric chars; we leave
        # 2 chars of headroom for safety.
        for user_id in ["abc", "user-123", "u" * 50, "🚀rocket🚀"]:
            for reason in [
                "alloc:job",
                "sweep:earn",
                "margin_restore",
                "manual" * 5,
                "",
            ]:
                cid = _generate_client_tran_id(user_id, reason)
                assert 1 <= len(cid) <= 32, (user_id, reason, cid)

    def test_alphanumeric_only(self) -> None:
        for user_id in ["with-dash", "with_underscore", "한글user", "ok"]:
            cid = _generate_client_tran_id(user_id, "alloc:job:42")
            assert cid.isalnum(), f"non-alphanumeric clientTranId: {cid!r}"

    def test_uniqueness_under_repeated_calls(self) -> None:
        # Idempotency is at the *caller* level (callers persist
        # ``client_tran_id`` before the API call). The generator itself
        # only guarantees uniqueness so collisions don't happen by
        # accident across retries from different code paths.
        seen = {
            _generate_client_tran_id("user-1", "alloc:job-abc")
            for _ in range(1000)
        }
        assert len(seen) == 1000

    def test_empty_inputs_still_yield_valid_id(self) -> None:
        cid = _generate_client_tran_id("", "")
        assert cid.isalnum()
        # "user" + "tx" + 14 hex chars = 20
        assert len(cid) == len("user") + len("tx") + 14


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
