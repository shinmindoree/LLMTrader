"""System prompts for LLM strategy generation. Redesign from scratch."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_PATH = _REPO_ROOT / "indicator_strategy_template.py"
_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "indicator-strategy" / "SKILL.md"
_VERIFY_SKILL_PATH = _REPO_ROOT / ".cursor" / "skills" / "strategy-verify" / "SKILL.md"
_EXAMPLE_STRATEGIES: tuple[Path, ...] = (
    _REPO_ROOT / "scripts" / "strategies" / "rsi_long_short_strategy.py",
    _REPO_ROOT / "scripts" / "strategies" / "macd_hist_immediate_entry_takeprofit_strategy.py",
)


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
    sections: list[str] = []
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
    examples = _load_example_strategies()
    sections: list[str] = []
    if skill:
        sections.append(str(skill))
    if verify_skill:
        sections.append(str(verify_skill))
    if template:
        sections.append(str(template))
    if examples:
        sections.append(str(examples))
    return "\n\n".join(sections) if sections else ""
