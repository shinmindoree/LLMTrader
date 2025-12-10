"""LLM으로 전략 코드 생성 스크립트."""

import asyncio
from pathlib import Path

import typer

from llmtrader.llm.pipeline import StrategyPipeline
from llmtrader.settings import get_settings

app = typer.Typer()


@app.command()
def generate(
    description: str = typer.Argument(..., help="전략 설명 (자연어)"),
    output: str = typer.Option(
        "generated_strategy.py",
        "--output",
        "-o",
        help="출력 파일 경로",
    ),
) -> None:
    """LLM으로 전략 코드 생성."""
    asyncio.run(_generate_async(description, output))


async def _generate_async(description: str, output: str) -> None:
    """비동기 생성 실행."""
    settings = get_settings()

    if not settings.openai.api_key:
        typer.echo("Error: OPENAI_API_KEY not set in .env", err=True)
        raise typer.Exit(1)

    typer.echo(f"Generating strategy from description:\n{description}\n")

    pipeline = StrategyPipeline(settings, max_retries=3)
    success, code, metadata = await pipeline.generate_and_validate(description)

    if success:
        output_path = Path(output)
        output_path.write_text(code, encoding="utf-8")
        typer.echo(f"\n✓ Strategy generated successfully: {output_path}")
        typer.echo(f"Attempts: {metadata['attempts']}")
        if metadata.get("lint_warnings"):
            typer.echo(f"Lint warnings: {len(metadata['lint_warnings'])}")
    else:
        typer.echo(f"\n✗ Strategy generation failed:\n{code}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()




