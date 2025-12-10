from llmtrader.app import create_app


def run() -> None:
    """uv 콘솔에서 실행될 엔트리포인트."""
    # uvicorn 명령을 그대로 사용하므로 여기서는 앱 객체만 노출한다.
    # CLI entry-point 예시: uv run uvicorn llmtrader.main:app --reload
    pass


app = create_app()




