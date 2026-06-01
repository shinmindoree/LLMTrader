"""Offline weekly funding-rate statistics -> Redis cache.

Loads per-symbol funding-rate Parquet from the Azure Blob data lake (written
by ``refresh_funding_parquet.py``), estimates a mean-reversion **half-life**
for each symbol, and stores a lightweight JSON blob per symbol in Redis so the
live engine only ever does an O(1) lookup.

Methodology (AR(1) / Ornstein-Uhlenbeck half-life)
--------------------------------------------------
Funding is a stationary mean-reverting series. Fit by OLS::

    Δf_t = α + β·f_{t-1} + ε_t

Then the half-life (in *settlement counts*) of a deviation from the mean is::

    half_life = -ln(2) / ln(1 + β)        (valid for -1 < β < 0)

This is preferred over fixed-threshold crossing because it is free of
arbitrary thresholds, uses the entire series, and is robust to noise. We also
persist ``r_squared`` and ``n_samples`` so the engine can fall back to a
conservative default when a symbol's fit is weak.

Redis schema::

    SET funding:stats:SOLUSDT '{"half_life_settlements":6.2,"avg_rate":0.045,
        "r_squared":0.31,"n_samples":2190,"updated":1730000000}'
    SET funding:stats:_universe '{"symbols":[...],"updated":...}'

Auth mirrors ``refresh_funding_parquet`` (blob) and ``oi_ingestor`` (redis).

Usage::

    python scripts/compute_funding_stats.py --blob-container market-data
    python scripts/compute_funding_stats.py --local-dir data/funding_rates --no-redis
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("compute_funding_stats")

DEFAULT_BLOB_CONTAINER = "market-data"
DEFAULT_BLOB_PREFIX = "funding-rates/version=1"
REDIS_KEY_FMT = "funding:stats:{symbol}"
REDIS_UNIVERSE_KEY = "funding:stats:_universe"

# Make ``src/common/redis_client`` importable regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------
def _blob_container_client(container_name: str):
    from azure.storage.blob import ContainerClient

    conn_str = os.environ.get("AZURE_BLOB_CONNECTION_STRING", "").strip()
    if conn_str:
        return ContainerClient.from_connection_string(conn_str, container_name)

    account_url = os.environ.get("AZURE_BLOB_ACCOUNT_URL", "").strip()
    if not account_url:
        raise RuntimeError("Set AZURE_BLOB_CONNECTION_STRING or AZURE_BLOB_ACCOUNT_URL.")
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    if os.environ.get("IDENTITY_ENDPOINT"):
        kwargs: dict = {}
        if client_id:
            kwargs["client_id"] = client_id
        credential = ManagedIdentityCredential(**kwargs)
    else:
        kwargs = {}
        if client_id:
            kwargs["managed_identity_client_id"] = client_id
        credential = DefaultAzureCredential(**kwargs)
    return ContainerClient(
        account_url=account_url, container_name=container_name, credential=credential
    )


def _symbol_from_blob(name: str) -> Optional[str]:
    for part in name.replace("\\", "/").split("/"):
        if part.startswith("symbol="):
            return part.split("=", 1)[1].upper()
    return None


def _iter_blob_frames(container, prefix: str):
    """Yield (symbol, DataFrame) for every ``data.parquet`` under ``prefix``."""
    import pandas as pd

    for blob in container.list_blobs(name_starts_with=prefix.rstrip("/") + "/"):
        if not blob.name.endswith("data.parquet"):
            continue
        sym = _symbol_from_blob(blob.name)
        if not sym:
            continue
        try:
            data = container.get_blob_client(blob.name).download_blob().readall()
            df = pd.read_parquet(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to read %s: %s", blob.name, exc)
            continue
        yield sym, df


def _iter_local_frames(local_dir: Path):
    import pandas as pd

    for path in sorted(local_dir.glob("symbol=*/data.parquet")):
        sym = _symbol_from_blob(str(path))
        if not sym:
            continue
        try:
            yield sym, pd.read_parquet(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to read %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def compute_half_life(df, *, min_samples: int = 50) -> Optional[dict]:
    """AR(1)/OU half-life of the funding-rate series in ``df``.

    Returns a stats dict, or ``None`` when there are too few observations.
    """
    import numpy as np

    if df is None or "funding_rate" not in df.columns:
        return None
    rates = (
        df.sort_values("funding_time")["funding_rate"].astype(float).to_numpy()
        if "funding_time" in df.columns
        else df["funding_rate"].astype(float).to_numpy()
    )
    rates = rates[np.isfinite(rates)]
    n = rates.size
    if n < min_samples:
        return None

    x = rates[:-1]
    y = rates[1:] - rates[:-1]  # Δf_t
    xm = x.mean()
    ym = y.mean()
    var_x = float(((x - xm) ** 2).sum())
    if var_x <= 0.0:
        return None
    beta = float(((x - xm) * (y - ym)).sum() / var_x)

    # r^2 of the regression
    y_hat = ym + beta * (x - xm)
    ss_res = float(((y - y_hat) ** 2).sum())
    ss_tot = float(((y - ym) ** 2).sum())
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # half-life: requires mean reversion, i.e. -1 < beta < 0
    if -1.0 < beta < 0.0:
        half_life = -math.log(2.0) / math.log(1.0 + beta)
    elif beta <= -1.0:
        half_life = 1.0  # over-damped / oscillatory -> reverts within ~1 settlement
    else:
        half_life = float("inf")  # beta >= 0 -> no mean reversion

    return {
        "half_life_settlements": (
            round(half_life, 2) if math.isfinite(half_life) else None
        ),
        "avg_rate": round(float(rates.mean()) * 100.0, 5),  # percent per settlement
        "beta": round(beta, 6),
        "r_squared": round(max(0.0, min(1.0, r_squared)), 4),
        "n_samples": int(n),
        "updated": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
def _connect_redis():
    from common.redis_client import (  # type: ignore[import-not-found]
        create_redis_client,
        create_redis_client_from_parts,
        create_redis_client_with_aad,
    )

    redis_url = os.environ.get("REDIS_URL", "").strip()
    redis_host = os.environ.get("REDIS_HOST", "").strip()
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    redis_username = os.environ.get("REDIS_USERNAME", "").strip()
    port = int(os.environ.get("REDIS_PORT", "6380"))
    ssl = os.environ.get("REDIS_SSL", "true").strip().lower() != "false"

    if redis_host and redis_username:
        rd = create_redis_client_with_aad(
            host=redis_host, username=redis_username, port=port, ssl=ssl,
            socket_connect_timeout=5, socket_timeout=5, decode_responses=True,
        )
    elif redis_host and redis_password:
        rd = create_redis_client_from_parts(
            host=redis_host, port=port, password=redis_password, ssl=ssl,
            socket_connect_timeout=5, socket_timeout=5, decode_responses=True,
        )
    elif redis_url:
        rd = create_redis_client(
            redis_url, socket_connect_timeout=5, socket_timeout=5, decode_responses=True,
        )
    else:
        raise RuntimeError(
            "Set REDIS_URL, REDIS_HOST+REDIS_USERNAME, or REDIS_HOST+REDIS_PASSWORD."
        )
    rd.ping()
    return rd


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(
    *,
    container=None,
    blob_prefix: str = DEFAULT_BLOB_PREFIX,
    local_dir: Optional[Path] = None,
    min_samples: int = 50,
    use_redis: bool = True,
    ttl_seconds: int = 14 * 24 * 3600,
) -> dict:
    if container is not None:
        frames = _iter_blob_frames(container, blob_prefix)
    elif local_dir is not None:
        frames = _iter_local_frames(local_dir)
    else:
        raise ValueError("Provide container or local_dir.")

    rd = _connect_redis() if use_redis else None

    stats_by_symbol: dict[str, dict] = {}
    skipped = 0
    for sym, df in frames:
        stats = compute_half_life(df, min_samples=min_samples)
        if stats is None:
            skipped += 1
            logger.info("[%s] skipped (insufficient data)", sym)
            continue
        stats_by_symbol[sym] = stats
        if rd is not None:
            rd.set(REDIS_KEY_FMT.format(symbol=sym), json.dumps(stats), ex=ttl_seconds)
        logger.info(
            "[%s] half_life=%s avg_rate=%.4f%% r2=%.3f n=%d",
            sym, stats["half_life_settlements"], stats["avg_rate"],
            stats["r_squared"], stats["n_samples"],
        )

    if rd is not None and stats_by_symbol:
        universe = sorted(stats_by_symbol.keys())
        rd.set(
            REDIS_UNIVERSE_KEY,
            json.dumps({"symbols": universe, "updated": int(time.time())}),
            ex=ttl_seconds,
        )

    logger.info(
        "done: computed=%d skipped=%d redis=%s",
        len(stats_by_symbol), skipped, use_redis,
    )
    return {"computed": len(stats_by_symbol), "skipped": skipped,
            "symbols": sorted(stats_by_symbol.keys())}


def _cli(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blob-container",
                        default=os.environ.get("FUNDING_BLOB_CONTAINER", DEFAULT_BLOB_CONTAINER))
    parser.add_argument("--blob-prefix",
                        default=os.environ.get("FUNDING_BLOB_PREFIX", DEFAULT_BLOB_PREFIX))
    parser.add_argument("--local-dir", default="",
                        help="read parquet from local filesystem instead of blob")
    parser.add_argument("--min-samples", type=int,
                        default=int(os.environ.get("FUNDING_MIN_SAMPLES", "50")))
    parser.add_argument("--no-redis", action="store_true",
                        help="compute and print only; do not write Redis")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)

    use_local = bool(args.local_dir)
    container = None if use_local else _blob_container_client(args.blob_container)
    local_dir = Path(args.local_dir) if use_local else None

    result = run(
        container=container,
        blob_prefix=args.blob_prefix,
        local_dir=local_dir,
        min_samples=args.min_samples,
        use_redis=not args.no_redis,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
