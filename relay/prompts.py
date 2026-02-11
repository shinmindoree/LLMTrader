"""System prompt for strategy code generation (indicator-strategy rules + template)."""

from pathlib import Path

from relay.capability_registry import capability_prompt_fragment

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "indicator_strategy_template.py"
_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "indicator-strategy" / "SKILL.md"
_VERIFY_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "strategy-verify" / "SKILL.md"
_EXAMPLE_STRATEGIES: tuple[Path, ...] = (
    _REPO_ROOT / "scripts" / "strategies" / "rsi_long_short_strategy.py",
    _REPO_ROOT / "scripts" / "strategies" / "macd_hist_immediate_entry_takeprofit_strategy.py",
)


INTAKE_SYSTEM_PROMPT = """You are an intake classifier for an algorithmic trading strategy builder.
Your task is to decide whether the user input is actionable for strategy code generation.

Return ONLY JSON with this schema:
{
  "intent": "OUT_OF_SCOPE" | "STRATEGY_CREATE" | "STRATEGY_MODIFY" | "STRATEGY_QA",
  "status": "READY" | "NEEDS_CLARIFICATION" | "UNSUPPORTED_CAPABILITY" | "OUT_OF_SCOPE",
  "user_message": "short Korean message for end user",
  "normalized_spec": {
    "symbol": string | null,
    "timeframe": string | null,
    "entry_logic": string | null,
    "exit_logic": string | null,
    "risk": object
  },
  "missing_fields": string[],
  "unsupported_requirements": string[],
  "clarification_questions": string[],
  "assumptions": string[],
  "development_requirements": string[]
}

Rules:
- If the input is unrelated to trading strategy generation/modification, set intent=OUT_OF_SCOPE and status=OUT_OF_SCOPE.
- If the strategy requires unsupported external infra/data pipelines (e.g. social media scraping, external sentiment engines), set status=UNSUPPORTED_CAPABILITY.
- For UNSUPPORTED_CAPABILITY, include actionable development_requirements (infra/components to build).
- Keep clarification_questions concise and concrete.
- user_message must be in Korean.
- Output valid JSON only. No markdown.

IMPORTANT — Be lenient about missing details:
- If the user mentions ANY indicator name or strategy concept (e.g. "RSI 과매도", "MACD 크로스", "볼린저밴드 전략", "이평선 골든크로스"), that IS sufficient entry/exit logic. Set status=READY and fill reasonable defaults in assumptions.
- Do NOT require explicit entry_logic/exit_logic text if the indicator/concept implies standard usage. Fill normalized_spec.entry_logic and exit_logic with inferred logic.
- symbol and timeframe are OPTIONAL — if missing, fill assumptions with sensible defaults (e.g. BTCUSDT, 1h) and still set status=READY.
- risk is ALWAYS optional. Never block on missing risk.
- Only set status=NEEDS_CLARIFICATION when the request is truly ambiguous (e.g. "전략 만들어줘" with zero indicator/concept hint).
- Prefer READY with assumptions over NEEDS_CLARIFICATION whenever possible."""


def _read_file(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _load_template_and_skill() -> tuple[str | None, str | None]:
    return _read_file(_TEMPLATE_PATH), _read_file(_SKILL_PATH)


def _load_verify_skill() -> str | None:
    return _read_file(_VERIFY_SKILL_PATH)


def _load_example_strategies() -> str:
    parts: list[str] = []
    for path in _EXAMPLE_STRATEGIES:
        content = _read_file(path)
        if content:
            parts.append(f"### {path.name}\n\n{content}")
    return "\n\n".join(parts)


def build_system_prompt() -> str:
    template, skill = _load_template_and_skill()
    examples = _load_example_strategies()

    if template and skill:
        sections = [
            "You generate a single Python file for a trading strategy.",
            "Output ONLY raw Python code. Do NOT wrap in markdown code fences (no ```).",
            "",
            "## Strategy Generation Rules (MUST follow)\n",
            skill,
            "",
            "## Reference template (copy helpers exactly, then implement your strategy class)\n",
            template,
        ]
        if examples:
            sections.extend([
                "",
                "## Example strategies (these are verified and working — follow the same patterns)\n",
                examples,
            ])
        return "\n".join(sections)

    fallback = (
        "You generate a single Python file for a trading strategy. "
        "The runner discovers a class whose name ends with \"Strategy\".\n\n"
        "- Output ONLY raw Python code. No markdown fences.\n"
        "- Class: PascalCase ending with \"Strategy\", inherit Strategy.\n"
        "- on_bar order: position reset -> open orders guard -> is_new_bar guard -> indicator -> prev init -> close -> entry -> prev update.\n"
    )
    if template:
        fallback += f"\n## Reference template\n\n{template}\n"
    if examples:
        fallback += f"\n## Example strategies\n\n{examples}\n"
    return fallback


def build_intake_system_prompt() -> str:
    return f"{INTAKE_SYSTEM_PROMPT}\n\n{capability_prompt_fragment()}"


def build_repair_system_prompt() -> str:
    template, skill = _load_template_and_skill()
    verify_skill = _load_verify_skill()
    examples = _load_example_strategies()

    sections = [
        "You fix Python trading strategy code so that it can be loaded and backtested.",
        "",
        "Requirements:",
        "- Output ONLY raw Python code for one strategy file. No markdown fences.",
        "- Preserve the user's original strategy intent as much as possible.",
        "- Return syntactically valid and executable code only.",
    ]

    if skill:
        sections.extend(["", "## Strategy Rules (code must comply)\n", skill])

    if verify_skill:
        sections.extend(["", "## Verification Checklist (code must pass all checks)\n", verify_skill])

    if template:
        sections.extend(["", "## Reference template (helpers must be copied exactly)\n", template])

    if examples:
        sections.extend(["", "## Working examples (follow these patterns)\n", examples])

    return "\n".join(sections)
