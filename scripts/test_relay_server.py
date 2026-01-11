"""중계 서버 연결 테스트 스크립트.

중계 서버(192.168.219.122:8000)의 연결 상태를 확인합니다.
"""

import sys
from pathlib import Path

import httpx

# 프로젝트 루트 설정
project_root = Path(__file__).parent.parent

# 기본 서버 주소
DEFAULT_RELAY_SERVER_URL = "http://192.168.219.122:8000"


async def test_relay_server(base_url: str = DEFAULT_RELAY_SERVER_URL) -> bool:
    """중계 서버 연결 테스트.

    Args:
        base_url: 중계 서버 기본 URL

    Returns:
        연결 성공 여부
    """
    print(f"🔌 중계 서버 연결 테스트: {base_url}")

    # 타임아웃 설정 (5초)
    timeout = httpx.Timeout(5.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 1. /docs 엔드포인트 확인 (FastAPI 기본)
            print(f"\n1️⃣ /docs 엔드포인트 확인...")
            try:
                response = await client.get(f"{base_url}/docs")
                if response.status_code == 200:
                    print(f"   ✅ /docs 접근 가능 (FastAPI 문서)")
                else:
                    print(f"   ⚠️  /docs 상태 코드: {response.status_code}")
            except httpx.TimeoutException:
                print(f"   ❌ /docs 타임아웃 (서버가 응답하지 않음)")
                return False
            except httpx.ConnectError as e:
                print(f"   ❌ /docs 연결 실패: {e}")
                return False
            except Exception as e:
                print(f"   ⚠️  /docs 접근 오류: {e}")

            # 2. /health 엔드포인트 확인 (일반적인 헬스체크)
            print(f"\n2️⃣ /health 엔드포인트 확인...")
            try:
                response = await client.get(f"{base_url}/health")
                if response.status_code == 200:
                    print(f"   ✅ /health 응답: {response.status_code}")
                    try:
                        data = response.json()
                        print(f"   응답 데이터: {data}")
                    except Exception:
                        print(f"   응답 텍스트: {response.text[:200]}")
                else:
                    print(f"   ⚠️  /health 상태 코드: {response.status_code}")
            except httpx.TimeoutException:
                print(f"   ⚠️  /health 타임아웃 (엔드포인트가 없을 수 있음)")
            except httpx.ConnectError:
                print(f"   ⚠️  /health 연결 실패 (엔드포인트가 없을 수 있음)")
            except Exception as e:
                print(f"   ⚠️  /health 접근 오류: {e}")

            # 3. /generate-strategy 엔드포인트 확인 (예상되는 엔드포인트)
            print(f"\n3️⃣ /generate-strategy 엔드포인트 확인...")
            try:
                # OPTIONS 또는 GET으로 엔드포인트 존재 여부 확인
                response = await client.options(f"{base_url}/generate-strategy")
                if response.status_code == 200:
                    print(f"   ✅ /generate-strategy 엔드포인트 존재 (OPTIONS)")
                elif response.status_code == 405:
                    print(f"   ✅ /generate-strategy 엔드포인트 존재 (405 Method Not Allowed - 정상)")
                else:
                    print(f"   ⚠️  /generate-strategy 상태 코드: {response.status_code}")
            except httpx.TimeoutException:
                print(f"   ⚠️  /generate-strategy 타임아웃 (엔드포인트가 없을 수 있음)")
            except httpx.ConnectError:
                print(f"   ⚠️  /generate-strategy 연결 실패 (엔드포인트가 없을 수 있음)")
            except Exception as e:
                print(f"   ⚠️  /generate-strategy 접근 오류: {e}")

            # 4. OpenAPI 스키마 확인
            print(f"\n4️⃣ /openapi.json 스키마 확인...")
            try:
                response = await client.get(f"{base_url}/openapi.json")
                if response.status_code == 200:
                    print(f"   ✅ OpenAPI 스키마 접근 가능")
                    try:
                        schema = response.json()
                        paths = schema.get("paths", {})
                        print(f"   발견된 엔드포인트:")
                        for path in sorted(paths.keys()):
                            methods = list(paths[path].keys())
                            print(f"     {path}: {', '.join(methods).upper()}")
                    except Exception as e:
                        print(f"   스키마 파싱 오류: {e}")
                else:
                    print(f"   ⚠️  OpenAPI 스키마 상태 코드: {response.status_code}")
            except httpx.TimeoutException:
                print(f"   ⚠️  OpenAPI 스키마 타임아웃")
            except httpx.ConnectError:
                print(f"   ⚠️  OpenAPI 스키마 연결 실패")
            except Exception as e:
                print(f"   ⚠️  OpenAPI 스키마 접근 오류: {e}")

        print(f"\n✅ 연결 테스트 완료")
        print(f"   참고: 일부 엔드포인트가 아직 구현되지 않았을 수 있습니다.")
        print(f"   API 문서: {base_url}/docs")
        return True

    except Exception as e:
        print(f"\n❌ 연결 테스트 실패: {e}")
        return False


async def main() -> None:
    """메인 함수."""
    import argparse

    parser = argparse.ArgumentParser(description="중계 서버 연결 테스트")
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_RELAY_SERVER_URL,
        help=f"중계 서버 URL (기본값: {DEFAULT_RELAY_SERVER_URL})",
    )

    args = parser.parse_args()

    success = await test_relay_server(base_url=args.url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
