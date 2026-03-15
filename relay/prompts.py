"""System prompts for LLM strategy generation."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "indicator_strategy_template.py"
_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "indicator-strategy" / "SKILL.md"
_VERIFY_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "strategy-verify" / "SKILL.md"
_STRATEGIES_DIR = _REPO_ROOT / "scripts" / "strategies"

_BASE_PATH = _REPO_ROOT / "src" / "strategy" / "base.py"
_CONTEXT_PATH = _REPO_ROOT / "src" / "strategy" / "context.py"
_AGENTS_MD_PATH = _REPO_ROOT / "src" / "strategy" / "AGENTS.md"

_DEFAULT_EXAMPLES: tuple[Path, ...] = (
    _REPO_ROOT / "scripts" / "strategies" / "rsi_long_short_strategy.py",
    _REPO_ROOT / "scripts" / "strategies" / "macd_hist_immediate_entry_takeprofit_strategy.py",
)

_INDICATOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "rsi": ("rsi", "relative strength", "과매수", "과매도", "oversold", "overbought"),
    "macd": ("macd", "이동평균수렴", "히스토그램", "histogram"),
    "ema": ("ema", "지수이동평균", "이동평균", "이평선", "exponential moving"),
    "bollinger": ("bollinger", "볼린저", "bb"),
    "stochastic": ("stochastic", "스토캐스틱", "stoch"),
    "williams": ("williams", "윌리엄스"),
    "turtle": ("turtle", "터틀", "돌파", "breakout"),
    "scalping": ("scalp", "스캘핑", "초단타"),
    "crossover": ("crossover", "크로스", "교차", "골든크로스", "데드크로스"),
    "momentum": ("momentum", "모멘텀"),
    "takeprofit": ("takeprofit", "익절", "take profit", "tp"),
}

INTAKE_SYSTEM_PROMPT = ""

SUMMARY_SYSTEM_PROMPT = ""

TEST_SYSTEM_PROMPT = ""


def build_strategy_chat_system_prompt(code: str, summary: str | None) -> str:
    return f"Strategy code:\n{code}\n\nSummary:\n{summary or 'N/A'}"


def _read_file(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _load_template_and_skill() -> tuple[str | None, str | None]:
    return _read_file(_TEMPLATE_PATH), _read_file(_SKILL_PATH)


def _load_verify_skill() -> str | None:
    return _read_file(_VERIFY_SKILL_PATH)


def _load_interface_docs() -> str:
    sections: list[str] = []
    base = _read_file(_BASE_PATH)
    if base:
        sections.append(f"### strategy/base.py\n\n```python\n{base}\n```")
    context = _read_file(_CONTEXT_PATH)
    if context:
        sections.append(f"### strategy/context.py\n\n```python\n{context}\n```")
    agents_md = _read_file(_AGENTS_MD_PATH)
    if agents_md:
        sections.append(f"### Strategy Interface Guide\n\n{agents_md}")
    return "\n\n".join(sections)


def _select_example_strategies(user_prompt: str, max_examples: int = 3) -> list[Path]:
    if not _STRATEGIES_DIR.is_dir():
        return [p for p in _DEFAULT_EXAMPLES if p.exists()]

    all_strategies = sorted(_STRATEGIES_DIR.glob("*_strategy.py"))
    if not all_strategies:
        return [p for p in _DEFAULT_EXAMPLES if p.exists()]

    prompt_lower = (user_prompt or "").lower()
    if not prompt_lower:
        return [p for p in _DEFAULT_EXAMPLES if p.exists()]

    scored: list[tuple[int, Path]] = []
    for path in all_strategies:
        name_lower = path.stem.lower()
        score = 0
        for indicator, keywords in _INDICATOR_KEYWORDS.items():
            if any(kw in prompt_lower for kw in keywords):
                if indicator in name_lower:
                    score += 3
        name_parts = name_lower.replace("_strategy", "").split("_")
        score += sum(1 for part in name_parts if len(part) > 2 and part in prompt_lower)
        scored.append((score, path))

    scored.sort(key=lambda x: (-x[0], x[1].name))

    selected = [path for score, path in scored if score > 0][:max_examples]

    for default in _DEFAULT_EXAMPLES:
        if len(selected) >= max_examples:
            break
        if default not in selected and default.exists():
            selected.append(default)

    if not selected:
        selected = [p for p in _DEFAULT_EXAMPLES if p.exists()]

    return selected


def _load_example_strategies(user_prompt: str = "") -> str:
    paths = (
        _select_example_strategies(user_prompt)
        if user_prompt
        else [p for p in _DEFAULT_EXAMPLES if p.exists()]
    )
    parts: list[str] = []
    for path in paths:
        content = _read_file(path)
        if content:
            parts.append(f"### {path.name}\n\n{content}")
    return "\n\n".join(parts)


def build_system_prompt(user_prompt: str = "") -> str:
    template, skill = _load_template_and_skill()
    interface = _load_interface_docs()
    examples = _load_example_strategies(user_prompt)
    sections: list[str] = []
    if interface:
        sections.append(f"## Strategy Interface\n\n{interface}")
    if template:
        sections.append(f"## Template\n\n{template}")
    if skill:
        sections.append(f"## Rules\n\n{skill}")
    if examples:
        sections.append(f"## Examples\n\n{examples}")
    return "\n\n".join(sections) if sections else ""


def build_intake_system_prompt() -> str:
    return INTAKE_SYSTEM_PROMPT


def build_repair_system_prompt() -> str:
    template, skill = _load_template_and_skill()
    verify_skill = _load_verify_skill()
    interface = _load_interface_docs()
    examples = _load_example_strategies()
    sections: list[str] = []
    if interface:
        sections.append(str(interface))
    if skill:
        sections.append(str(skill))
    if verify_skill:
        sections.append(str(verify_skill))
    if template:
        sections.append(str(template))
    if examples:
        sections.append(str(examples))
    return "\n\n".join(sections) if sections else ""
