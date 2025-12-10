from fastapi import Depends, FastAPI

from llmtrader.settings import Settings, get_settings
from llmtrader.api.routers import router as binance_router


def create_app() -> FastAPI:
    """FastAPI 애플리케이션을 생성한다."""
    app = FastAPI(title="LLMTrader API", version="0.1.0")

    @app.get("/healthz")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    async def status(settings: Settings = Depends(get_settings)) -> dict[str, str]:
        return {
            "env": settings.env,
            "binance_base_url": settings.binance.base_url,
        }

    app.include_router(binance_router)

    return app


app = create_app()

