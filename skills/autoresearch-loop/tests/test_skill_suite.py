import json
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).parents[1]
WORKER_SKILLS = (
    "research-literature-review",
    "research-paper-analysis",
    "research-hypothesis-generation",
    "research-experiment-design",
    "research-experiment-execution",
    "research-experiment-evaluation",
    "research-evidence-critique",
    "research-result-synthesis",
)

WORKER_REQUIRED = {
    "research-literature-review": (
        "search plan",
        "inclusion",
        "exclusion",
        "evidence card",
        "citation",
        "accepted",
        "publication status",
        "official proceedings",
        "preprint",
        "ScholarAPI",
        "planner-decision",
        "not_selected",
        "primary-source",
    ),
    "research-paper-analysis": (
        "claim map",
        "method",
        "reproduction",
        "limitations",
        "unverified",
    ),
    "research-hypothesis-generation": (
        "falsifiable",
        "expected observation",
        "falsification condition",
        "exploit",
        "explore",
        "falsify",
    ),
    "research-experiment-design": (
        "dataset split",
        "data leakage",
        "Initial Baseline",
        "primary metric",
        "noise",
        "Budget Slice",
        "dataset-audit task",
        "baseline-contract task",
        "candidate-contract",
    ),
    "research-experiment-execution": (
        "Modification Envelope",
        "Budget Slice",
        "smoke",
        "command receipt",
        "failure class",
        "report.md",
    ),
    "research-experiment-evaluation": (
        "partial blinding",
        "hard gates",
        "uncertainty",
        "confounds",
        "cannot modify",
        "verdict recommendation",
    ),
    "research-evidence-critique": (
        "alternative explanation",
        "data leakage",
        "weak baseline",
        "confound",
        "disconfirming evidence",
    ),
    "research-result-synthesis": (
        "claim-evidence",
        "negative result",
        "inconclusive",
        "limitations",
        "citation verification",
    ),
}


def assert_reference(name: str, required: tuple[str, ...]) -> None:
    text = (ROOT / "references" / name).read_text(encoding="utf-8")
    missing = [term for term in required if term not in text]
    assert not missing, f"{name} missing: {missing}"


def test_protocol_references_cover_owned_invariants():
    contracts = (
        (
            "research-state.md",
            (
                "Coordinator",
                "revision",
                "compare-and-swap",
                "checkpoint",
                "state.json",
            ),
        ),
        (
            "artifact-store.md",
            (
                "immutable",
                "SHA-256",
                "parent artifact",
                "provenance",
                "index.jsonl",
            ),
        ),
        (
            "task-stage-protocol.md",
            (
                "research.yaml",
                "preconditions",
                "acceptance criteria",
                "gate",
                "next",
            ),
        ),
        (
            "testing-strategy.md",
            (
                "focused",
                "Phase checkpoint",
                "full suite",
                "failure injection",
            ),
        ),
        (
            "environment-executor.md",
            (
                "uv-local",
                "command-local",
                "shell=False",
                "timeout",
                "no silent fallback",
                "must not launch an LLM",
            ),
        ),
        (
            "evaluator.md",
            (
                "EvaluationSpec",
                "hard gates",
                "partial blinding",
                "confounds",
                "cannot edit",
            ),
        ),
        (
            "search-policy.md",
            (
                "exploit",
                "explore",
                "falsify",
                "debug depth",
                "Provisional Best",
                "Current Best",
                "stop",
            ),
        ),
        (
            "research-standards.md",
            (
                "falsifiable",
                "data leakage",
                "Initial Baseline",
                "Current Best",
                "negative result",
                "citation verification",
                "official proceedings",
            ),
        ),
        (
            "prompt-contracts.md",
            (
                "Role Boundary",
                "Context Manifest",
                "Budget Slice",
                "Forbidden Actions",
                "report.md",
                "must not spawn subagents",
                "complete chat history is forbidden",
                "autoresearch-record.jsonl",
            ),
        ),
    )
    for name, required in contracts:
        assert_reference(name, required)


def assert_worker_skill(name: str, required: tuple[str, ...]) -> None:
    skill_root = ROOT / "skills" / name
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    lowered = skill.lower()
    missing = [term for term in required if term.lower() not in lowered]
    assert not missing, f"{name} missing: {missing}"
    for heading in (
        "## Overview",
        "## When to Use",
        "## Process",
        "## Common Rationalizations",
        "## Red Flags",
        "## Verification",
    ):
        assert heading in skill
    assert "report.md" in skill
    assert "must not edit global state" in lowered
    assert "must not edit the model-call ledger" in lowered

    metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert f"${name}" in metadata
    assert "allow_implicit_invocation: false" in metadata

    rows = [
        json.loads(line)
        for line in (skill_root / "routing-eval.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert sum(row.get("expected_skill") == name for row in rows) >= 3
    assert sum(name in row.get("negative_for", []) for row in rows) >= 2
    assert sum(bool(row.get("expectations")) for row in rows) >= 1


def test_embedded_planner_is_not_a_top_level_capability():
    planner = ROOT / "skills" / "planner"
    scholarapi = planner / "skills" / "scholarapi"

    assert not (ROOT.parent / "scholarapi").exists()
    assert (planner / "SKILL.md").is_file()
    assert (scholarapi / "SKILL.md").is_file()
    assert (scholarapi / "scholarapi.py").is_file()

    planner_text = (planner / "SKILL.md").read_text(encoding="utf-8")
    for phrase in (
        "candidate discovery",
        "mandatory bounded literature preflight",
        "Change Scope",
        "Research Plan",
        "Evidence Registry",
        "Task Context Pack",
        "Modification Envelope",
        "SCHOLARAPI_API_KEY",
        "query count, page count, and result count",
        "not_selected",
        "planner-decision",
        "literature-metadata",
    ):
        assert phrase in planner_text
    assert "Rick" not in planner_text


def test_worker_skill_contracts_and_composition():
    for name, required in WORKER_REQUIRED.items():
        assert_worker_skill(name, required=required)

    profile = yaml.safe_load(
        (ROOT / "assets" / "research-project" / "research.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert profile["limits"]["max_capability_skills"] == 2
    for stage in profile["stages"]:
        capabilities = stage["capability_skills"]
        assert len(capabilities) <= 2, stage["id"]
        assert not set(capabilities).intersection(WORKER_SKILLS), stage["id"]
        assert "autoresearch-loop" not in capabilities, stage["id"]

    for name in WORKER_SKILLS:
        root = ROOT / "skills" / name
        skill = (root / "SKILL.md").read_text(encoding="utf-8").lower()
        metadata = (root / "agents" / "openai.yaml").read_text(encoding="utf-8")

        assert "task-local `report.md`" in skill, name
        assert "subagent" in skill, name
        assert re.search(r"never.{0,120}subagent", skill, flags=re.DOTALL), name
        assert "allow_implicit_invocation: false" in metadata, name
        assert f"default_prompt: \"use ${name} " in metadata.lower(), name

    for skill_path in (ROOT / "skills").glob("*/SKILL.md"):
        assert "Rick" not in skill_path.read_text(encoding="utf-8"), skill_path
