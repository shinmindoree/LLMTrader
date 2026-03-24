"""Blob Storage 전략 파일 업로드 → 다운로드 → 로드 → 실행 검증 스크립트.

Usage:
    # Azurite 에뮬레이터 사용 (기본)
    uv run python scripts/verify_blob_strategy.py

    # 실제 Azure Blob 연결문자열 사용
    uv run python scripts/verify_blob_strategy.py --connection-string "DefaultEndpoints..."

    # Account URL (Managed Identity) 사용
    uv run python scripts/verify_blob_strategy.py --account-url "https://<account>.blob.core.windows.net"
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ── 테스트용 전략 코드 ────────────────────────────────────
SAMPLE_STRATEGY_CODE = textwrap.dedent("""\
    from typing import Any
    from strategy.base import Strategy
    from strategy.context import StrategyContext

    STRATEGY_PARAMS: dict[str, Any] = {"fast_period": 5, "slow_period": 20}

    class BlobTestStrategy(Strategy):
        \"\"\"Blob 검증용 더미 전략.\"\"\"

        def __init__(self, fast_period: int = 5, slow_period: int = 20) -> None:
            super().__init__()
            self.fast_period = fast_period
            self.slow_period = slow_period

        def initialize(self, ctx: StrategyContext) -> None:
            pass

        def on_bar(self, ctx: StrategyContext, bar: dict[str, Any]) -> None:
            pass
""")

AZURITE_CONN_STR = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsu"
    "Fq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1"
)

TEST_USER_ID = "test-user-blob-verify"
TEST_STRATEGY_NAME = "blob_test_strategy"


def _print_step(n: int, msg: str) -> None:
    print(f"\n{'='*60}")
    print(f"  Step {n}: {msg}")
    print(f"{'='*60}")


def _print_ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _print_fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Blob Storage 전략 검증")
    parser.add_argument("--connection-string", default=None, help="Azure Blob connection string")
    parser.add_argument("--account-url", default=None, help="Azure Blob account URL")
    parser.add_argument("--container", default="strategies", help="Container name")
    args = parser.parse_args()

    conn_str = args.connection_string
    account_url = args.account_url

    # 환경변수 / .env 에서 먼저 가져오기 시도
    if not conn_str and not account_url:
        try:
            from settings import get_settings
            s = get_settings()
            conn_str = s.azure_blob.connection_string.strip() or None
            account_url = s.azure_blob.account_url.strip() or None
        except Exception:
            pass

    # 아무 설정도 없으면 Azurite 사용
    if not conn_str and not account_url:
        print("[INFO] Azure Blob 설정이 없습니다. Azurite 에뮬레이터로 테스트합니다.")
        conn_str = AZURITE_CONN_STR

    # ── Step 1: Blob Service 연결 ──────────────────────────
    _print_step(1, "StrategyBlobService 연결")
    from common.blob_storage import StrategyBlobService

    try:
        if account_url:
            svc = StrategyBlobService.from_account_url(account_url, args.container)
            _print_ok(f"from_account_url 사용: {account_url}")
        else:
            svc = StrategyBlobService.from_connection_string(conn_str, args.container)
            is_azurite = "devstoreaccount1" in (conn_str or "")
            label = "Azurite" if is_azurite else "Azure Blob"
            _print_ok(f"from_connection_string 사용 ({label})")
    except Exception as exc:
        _print_fail(f"연결 실패: {exc}")
        print("\n  Azurite가 실행 중인지 확인하세요:")
        print("    docker run -p 10000:10000 mcr.microsoft.com/azure-storage/azurite azurite-blob --blobHost 0.0.0.0")
        sys.exit(1)

    # 컨테이너 존재 확인 & 생성
    try:
        if not svc._container.exists():
            svc._container.create_container()
            _print_ok("컨테이너 생성 완료")
        else:
            _print_ok("컨테이너 이미 존재")
    except Exception as exc:
        _print_fail(f"컨테이너 확인/생성 실패: {exc}")
        print("\n  Azurite가 실행 중인지 확인하세요.")
        sys.exit(1)

    # ── Step 2: 전략 업로드 ────────────────────────────────
    _print_step(2, "전략 코드 업로드")
    try:
        blob_path = svc.upload(TEST_USER_ID, TEST_STRATEGY_NAME, SAMPLE_STRATEGY_CODE)
        _print_ok(f"업로드 성공: blob_path={blob_path}")
    except Exception as exc:
        _print_fail(f"업로드 실패: {exc}")
        sys.exit(1)

    # ── Step 3: 전략 다운로드 & 무결성 검증 ──────────────────
    _print_step(3, "전략 코드 다운로드 & 검증")
    try:
        downloaded = svc.download(TEST_USER_ID, TEST_STRATEGY_NAME)
        if downloaded == SAMPLE_STRATEGY_CODE:
            _print_ok("다운로드 코드 == 업로드 코드 (무결성 통과)")
        else:
            _print_fail("다운로드 코드 불일치!")
            print(f"  업로드 길이: {len(SAMPLE_STRATEGY_CODE)}, 다운로드 길이: {len(downloaded)}")
            sys.exit(1)
    except Exception as exc:
        _print_fail(f"다운로드 실패: {exc}")
        sys.exit(1)

    # download_by_path 도 검증
    try:
        downloaded2 = svc.download_by_path(blob_path)
        assert downloaded2 == SAMPLE_STRATEGY_CODE
        _print_ok("download_by_path 도 정상")
    except Exception as exc:
        _print_fail(f"download_by_path 실패: {exc}")
        sys.exit(1)

    # ── Step 4: 전략 목록 조회 ────────────────────────────
    _print_step(4, "전략 목록 조회 (list_strategies)")
    try:
        strategies = svc.list_strategies(TEST_USER_ID)
        names = [s["strategy_name"] for s in strategies]
        _print_ok(f"목록: {names}")
        assert any(TEST_STRATEGY_NAME in n for n in names), f"{TEST_STRATEGY_NAME} not in list"
    except Exception as exc:
        _print_fail(f"목록 조회 실패: {exc}")
        sys.exit(1)

    # ── Step 5: resolve_strategy_file + load_strategy_class 테스트 ──
    _print_step(5, "resolve_strategy_file → load_strategy_class → build_strategy")
    from runner.strategy_loader import build_strategy, load_strategy_class, resolve_strategy_file

    repo_root = Path(__file__).resolve().parents[1]
    fake_path = f"scripts/strategies/{TEST_STRATEGY_NAME}.py"

    try:
        strategy_file, was_materialized = resolve_strategy_file(
            repo_root=repo_root,
            strategy_path=fake_path,
            fallback_code=downloaded,
        )
        _print_ok(f"resolve_strategy_file → {strategy_file.name} (materialized={was_materialized})")
    except Exception as exc:
        _print_fail(f"resolve_strategy_file 실패: {exc}")
        sys.exit(1)

    try:
        strategy_class = load_strategy_class(strategy_file)
        _print_ok(f"load_strategy_class → {strategy_class.__name__}")
        assert strategy_class.__name__ == "BlobTestStrategy"
    except Exception as exc:
        _print_fail(f"load_strategy_class 실패: {exc}")
        sys.exit(1)

    try:
        instance = build_strategy(strategy_class, {"fast_period": 10, "slow_period": 30})
        _print_ok(f"build_strategy → fast={instance.fast_period}, slow={instance.slow_period}")
        assert instance.fast_period == 10
        assert instance.slow_period == 30
    except Exception as exc:
        _print_fail(f"build_strategy 실패: {exc}")
        sys.exit(1)

    # 임시 파일 정리
    if was_materialized:
        strategy_file.unlink(missing_ok=True)
        _print_ok("임시 전략 파일 정리 완료")

    # ── Step 6: get_strategy_storage() 통합 테스트 ─────────
    _print_step(6, "get_strategy_storage() → StrategyStorage 프로토콜 확인")
    from common.strategy_storage import StrategyStorage, get_strategy_storage

    # get_blob_service 캐시 초기화 (이미 초기화된 경우 테스트에 영향)
    import common.blob_storage as _bs
    _bs._blob_service_initialized = False
    _bs._blob_service_cache = None

    storage = get_strategy_storage()
    if storage is not None and isinstance(storage, StrategyStorage):
        _print_ok(f"get_strategy_storage() → {type(storage).__name__}")
        try:
            code = storage.download_by_path(blob_path)
            assert code == SAMPLE_STRATEGY_CODE
            _print_ok("download_by_path via StrategyStorage 프로토콜 성공")
        except Exception as exc:
            _print_fail(f"StrategyStorage 다운로드 실패: {exc}")
    else:
        print("  [SKIP] get_strategy_storage() == None (환경변수 미설정 — Azurite 직접 테스트만 수행)")

    # ── Step 7: 정리 (삭제) ────────────────────────────────
    _print_step(7, "테스트 전략 삭제")
    try:
        deleted = svc.delete(TEST_USER_ID, TEST_STRATEGY_NAME)
        _print_ok(f"삭제 결과: {deleted}")
    except Exception as exc:
        _print_fail(f"삭제 실패: {exc}")

    # ── 최종 결과 ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  ALL CHECKS PASSED — Blob Storage 전략 로딩 & 실행 정상 ✓")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
