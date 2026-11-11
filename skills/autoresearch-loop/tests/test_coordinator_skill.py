import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_coordinator_contract_and_routing_are_bounded():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 365

    required = (
        "Change Scope",
        "Resource Budget",
        "Evaluation Contract",
        "Research Plan",
        "Evidence Registry",
        "Task Context Pack",
        "Task Budget Allocation",
        "Experiment Run Contract",
        "Parallel Worker Limit",
        "research-literature-review",
        "ScholarAPI preflight",
        "not_selected",
        "Modification Envelope",
        "Budget Envelope",
        "gate_authority: coordinator | user",
        "optional\naudit metadata",
        "ResearchState",
        "ArtifactStore",
        "Task/Stage Protocol",
        "EnvironmentExecutor",
        "Evaluator",
        "SearchPolicy",
        "loop-subagent-bound",
        "must not spawn subagents",
        "one primary skill",
        "at most two capability skills",
        "brief.md",
        "report.md",
        "autoresearch-record.jsonl",
    )
    missing = [phrase for phrase in required if phrase not in text]
    assert not missing, f"Coordinator missing: {missing}"
    assert "Rick" not in text
    for obsolete in (
        "does not run training, evaluation, or the loop",
        "program.md is the execution contract",
    ):
        assert obsolete not in text

    metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "Autoresearch Loop"' in metadata
    assert "$autoresearch-loop" in metadata
    assert "allow_implicit_invocation: true" in metadata

    rows = [
        json.loads(line)
        for line in (ROOT / "routing-eval.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert sum(row.get("expected_skill") == "autoresearch-loop" for row in rows) >= 3
    assert sum("autoresearch-loop" in row.get("negative_for", []) for row in rows) >= 2
    assert any(bool(row.get("expectations")) for row in rows)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "Plain-language names",
        "ScholarAPI request rules",
        "GET /search",
        "GET /list",
        "next_cursor",
        "X-Request-Id",
        "literature-metadata",
        "Change Scope",
        "Resource Budget",
        "Evaluation Contract",
        "Research Plan",
        "Evidence Registry",
        "Task Context Pack",
        "Task Budget Allocation",
        "Parallel Worker Limit",
        "Coordinator-internal",
        "uv run skills/autoresearch-loop/scripts/researchctl.py init",
        "uv run skills/autoresearch-loop/scripts/researchctl.py validate",
        "uv run skills/autoresearch-loop/scripts/researchctl.py status",
        ".research/autoresearch-record.jsonl",
        "Modification Envelope",
        "Budget Envelope",
        "gate_authority: coordinator | user",
        "Missing provider usage is not a completion",
        "local executors only",
        "no cryptographic user identity",
    ):
        assert phrase in readme
    assert "Rick" not in readme
