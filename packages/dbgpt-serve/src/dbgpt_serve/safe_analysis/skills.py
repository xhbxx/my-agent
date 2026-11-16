"""Trusted analysis procedures, explicitly selected by name.

A Skill supplies domain instructions, not permissions and not executable code.
There is deliberately no model-selected file path or shell command here.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .contracts import AnalysisError


@dataclass(frozen=True)
class AnalysisSkill:
    name: str
    instructions: str


SKILL_NAMES = {"general", "trend", "comparison"}


@lru_cache(maxsize=3)
def get_skill(name):
    if name not in SKILL_NAMES:
        raise AnalysisError("未知 Skill；可选 general/trend/comparison")
    path = Path(__file__).with_name("skills") / name / "SKILL.md"
    return AnalysisSkill(name, path.read_text(encoding="utf-8"))
