from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoresearch.contracts import JsonObject


def preflight_graph_skills(
    graph: Mapping[str, Any], *, repository: Path | None = None
) -> None:
    stages = graph.get("stages")
    if not isinstance(stages, list):
        raise ValueError("graph skill preflight requires graph stages")
    skill_names = list(
        dict.fromkeys(
            skill_name
            for stage in stages
            if isinstance(stage, Mapping)
            for skill_name in (
                stage.get("primary_skill"),
                *(stage.get("capability_skills") or []),
            )
            if isinstance(skill_name, str)
        )
    )
    for skill_name in skill_names:
        if not _skill_exists(skill_name, repository):
            raise FileNotFoundError(f"SKILL.md not found for skill {skill_name}")


def _skill_exists(
    skill_name: str, repository: Path | None
) -> bool:
    return any(
        (root / skill_name / "SKILL.md").is_file()
        for root in _skill_roots((repository or _repository_root()).resolve())
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _skill_roots(repository: Path) -> tuple[Path, ...]:
    return tuple(
        dict.fromkeys(
            (
                repository / "skills",
                Path.home() / ".codex" / "skills",
                Path.home() / ".agents" / "skills",
            )
        )
    )
