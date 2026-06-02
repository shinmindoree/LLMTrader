"""전략 파라미터 아티팩트 스토어 (전략-비종속).

전략별 / 심볼별로 최적화된 파라미터셋을 저장·조회하는 범용 스토어다.
키는 ``(strategy_id, symbol)`` 이며, 전략명을 코드가 아닌 데이터로 다룬다.

설계 의도
---------
- MFP 같은 멀티-leg 전략은 leg **구조**(family / interval / lookback / feature
  flag / side)를 코드의 baseline 으로 고정하고, 심볼별로 **임계값**(tp/sl/z/rsi/
  atr/max_hold 등)만 재최적화한다. 그 임계값 재피팅 결과가 여기에 저장된다.
- 아티팩트는 ``leg_overrides`` (leg 당 임계값 override dict 리스트) 또는 임의
  ``params`` (단일-시그널 전략용)를 담을 수 있다. 스토어는 내용 해석에 관여하지
  않으며 ``(strategy_id, symbol)`` 키-값 저장만 책임진다.

상태(status) 흐름
-----------------
``validated`` (OOS 게이트 통과) → ``promoted`` (라이브 자격). ``load_promoted``
는 promoted 상태만 반환한다.

저장 위치 (조회 우선순위)
-------------------------
1. 환경변수 경로 override:  ``STRATEGY_PARAMS_DIR``
2. 로컬 파일:               ``<repo>/data/strategy_params/<strategy_id>/<SYMBOL>.json``
3. Azure Blob 폴백:         컨테이너 ``STRATEGY_PARAMS_BLOB_CONTAINER`` 의
                            ``<prefix>/<strategy_id>/<SYMBOL>.json``
                            (prefix = ``STRATEGY_PARAMS_BLOB_PREFIX``, 기본
                            ``strategy_params``)

JSON 스키마 예시
----------------
::

    {
      "strategy_id": "multi_factor_portfolio",
      "symbol": "ETHUSDT",
      "version": 1,
      "status": "promoted",
      "leg_overrides": [ {"tp_pct": 0.018, "sl_pct": 0.010, ...}, ... ],
      "oos": {
        "train_window": ["2023-04-01", "2025-04-30"],
        "test_window":  ["2025-05-01", "2026-04-29"],
        "metrics": { ... },
        "accepted_at": "2026-06-03T00:00:00Z"
      },
      "created_at": "2026-06-03T00:00:00Z"
    }
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("llmtrader.param_store")

# src/strategy/param_store.py -> repo root is parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOCAL_DIR = _REPO_ROOT / "data" / "strategy_params"

STATUS_VALIDATED = "validated"
STATUS_PROMOTED = "promoted"


@dataclass
class ParamArtifact:
    """파라미터 아티팩트 (저장 단위)."""

    strategy_id: str
    symbol: str
    status: str = STATUS_VALIDATED
    version: int = 1
    leg_overrides: list[dict[str, Any]] | None = None
    params: dict[str, Any] | None = None
    oos: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "status": self.status,
            "version": self.version,
        }
        if self.leg_overrides is not None:
            d["leg_overrides"] = self.leg_overrides
        if self.params is not None:
            d["params"] = self.params
        if self.oos:
            d["oos"] = self.oos
        if self.created_at:
            d["created_at"] = self.created_at
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ParamArtifact:
        return cls(
            strategy_id=str(d["strategy_id"]),
            symbol=str(d["symbol"]).upper(),
            status=str(d.get("status", STATUS_VALIDATED)),
            version=int(d.get("version", 1)),
            leg_overrides=d.get("leg_overrides"),
            params=d.get("params"),
            oos=d.get("oos", {}) or {},
            created_at=d.get("created_at"),
        )


def _safe_symbol(symbol: str) -> str:
    return str(symbol).upper().replace("/", "_").replace("\\", "_")


def _local_dir() -> Path:
    override = os.environ.get("STRATEGY_PARAMS_DIR", "").strip()
    return Path(override) if override else _DEFAULT_LOCAL_DIR


def _local_path(strategy_id: str, symbol: str) -> Path:
    return _local_dir() / strategy_id / f"{_safe_symbol(symbol)}.json"


def _blob_name(strategy_id: str, symbol: str) -> str:
    prefix = os.environ.get("STRATEGY_PARAMS_BLOB_PREFIX", "strategy_params").strip().rstrip("/")
    leaf = f"{strategy_id}/{_safe_symbol(symbol)}.json"
    return f"{prefix}/{leaf}" if prefix else leaf


def _blob_container_client(container: str) -> Any | None:
    """임의 컨테이너에 대한 Azure Blob ContainerClient 생성.

    ``common.blob_storage`` 의 자격증명 해석(연결 문자열 / account_url +
    managed identity)을 그대로 재사용하되 대상 컨테이너만 바꾼다.
    """
    try:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
        from azure.storage.blob import ContainerClient

        from settings import get_settings
    except Exception:  # noqa: BLE001
        return None

    settings = get_settings()
    conn_str = settings.azure_blob.connection_string.strip()
    account_url = settings.azure_blob.account_url.strip()
    try:
        if conn_str:
            return ContainerClient.from_connection_string(conn_str, container)
        if account_url:
            client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
            if os.getenv("IDENTITY_ENDPOINT"):
                kwargs: dict[str, Any] = {"client_id": client_id} if client_id else {}
                credential: Any = ManagedIdentityCredential(**kwargs)
            else:
                kwargs = {"managed_identity_client_id": client_id} if client_id else {}
                credential = DefaultAzureCredential(**kwargs)
            return ContainerClient(account_url=account_url, container_name=container,
                                   credential=credential)
    except Exception:  # noqa: BLE001
        logger.debug("param_store: blob container client init failed", exc_info=True)
    return None


def _load_from_blob(strategy_id: str, symbol: str) -> dict[str, Any] | None:
    container = os.environ.get("STRATEGY_PARAMS_BLOB_CONTAINER", "").strip()
    if not container:
        return None
    client = _blob_container_client(container)
    if client is None:
        return None
    try:
        raw = client.download_blob(_blob_name(strategy_id, symbol)).readall()
        return json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        logger.debug("param_store: blob load failed for %s/%s", strategy_id, symbol,
                     exc_info=True)
        return None


def load_artifact(strategy_id: str, symbol: str) -> ParamArtifact | None:
    """``(strategy_id, symbol)`` 아티팩트를 로드한다. 없으면 None.

    상태(status)와 무관하게 발견된 아티팩트를 반환한다. 라이브 자격 게이트가
    필요하면 ``load_promoted`` 를 사용하라.
    """
    sym = _safe_symbol(symbol)

    # 1) explicit env path / local file
    local = _local_path(strategy_id, sym)
    if local.exists():
        try:
            return ParamArtifact.from_dict(json.loads(local.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            logger.warning("param_store: failed to parse %s", local, exc_info=True)
            return None

    # 2) blob fallback
    blob_dict = _load_from_blob(strategy_id, sym)
    if blob_dict is not None:
        try:
            return ParamArtifact.from_dict(blob_dict)
        except Exception:  # noqa: BLE001
            logger.warning("param_store: failed to parse blob artifact for %s/%s",
                           strategy_id, sym, exc_info=True)
    return None


def load_promoted(strategy_id: str, symbol: str) -> ParamArtifact | None:
    """promoted 상태의 아티팩트만 반환한다. 없거나 비-promoted 면 None."""
    art = load_artifact(strategy_id, symbol)
    if art is None:
        return None
    if art.status != STATUS_PROMOTED:
        logger.info("param_store: artifact for %s/%s exists but status=%s (not promoted)",
                    strategy_id, symbol, art.status)
        return None
    return art


def save(artifact: ParamArtifact) -> Path:
    """아티팩트를 로컬 스토어에 저장하고 경로를 반환한다.

    Blob 업로드는 호출자가 명시적으로 수행한다(프로비저닝 스크립트). 여기서는
    버전 관리·검증이 쉬운 로컬 JSON 기록만 책임진다.
    """
    path = _local_path(artifact.strategy_id, artifact.symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("param_store: saved %s/%s (status=%s) -> %s",
                artifact.strategy_id, artifact.symbol, artifact.status, path)
    return path


def has_promoted(strategy_id: str, symbol: str) -> bool:
    """promoted 아티팩트 존재 여부."""
    return load_promoted(strategy_id, symbol) is not None
