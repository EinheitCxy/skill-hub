#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


MODES = ("orient", "train", "resume", "evaluate", "debug", "package")
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}
SECRET_PATTERNS = (
    re.compile(r"^\.env(?:\.|$)"),
    re.compile(r"(?:^|[_-])(?:secret|secrets|credential|credentials|token|tokens)(?:[_\-.]|$)"),
    re.compile(r"(?:^|[_-])private[_-]?key(?:[_\-.]|$)"),
    re.compile(r"^id_(?:rsa|dsa|ecdsa|ed25519)"),
    re.compile(r"\.(?:pem|p12|pfx|key)$"),
)
CONFIG_NAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "uv.lock",
    "poetry.lock",
    "environment.yml",
    "environment.yaml",
}


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


def find_git_root(path: Path) -> Path | None:
    result = run_git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def git_facts(root: Path | None) -> dict[str, object]:
    if root is None:
        return {"available": False, "branch": None, "head": None, "dirty": None, "status": []}
    branch_result = run_git(root, "rev-parse", "--abbrev-ref", "HEAD")
    head_result = run_git(root, "rev-parse", "HEAD")
    status_result = run_git(root, "status", "--short")
    status = [line for line in status_result.stdout.splitlines() if line.strip()]
    return {
        "available": True,
        "branch": branch_result.stdout.strip() if branch_result.returncode == 0 else None,
        "head": head_result.stdout.strip() if head_result.returncode == 0 else None,
        "dirty": bool(status),
        "status": status,
    }


def is_secret_like(path: Path) -> bool:
    lower = path.name.casefold()
    return any(pattern.search(lower) for pattern in SECRET_PATTERNS)


def applicable_instructions(root: Path, target: Path) -> list[str]:
    instructions: list[str] = []
    current = root
    relative_parts = target.relative_to(root).parts if target != root else ()
    candidates = [root]
    for part in relative_parts:
        current = current / part
        if current.is_dir():
            candidates.append(current)
    for directory in candidates:
        for name in ("AGENTS.md", "CLAUDE.md"):
            candidate = directory / name
            if candidate.is_file() and not is_secret_like(candidate):
                instructions.append(candidate.relative_to(root).as_posix())
    return sorted(set(instructions))


def classify(relative: Path) -> set[str]:
    lower = relative.as_posix().casefold()
    name = relative.name.casefold()
    stem = relative.stem.casefold()
    parts = {part.casefold() for part in relative.parts}
    categories: set[str] = set()

    if name.startswith("readme"):
        categories.add("documentation")
    if (
        name in CONFIG_NAMES
        or name.startswith(("requirements", "environment"))
        or "config" in parts
        or "configs" in parts
    ):
        categories.add("configuration")
    if re.search(r"(?:^|[_-])(?:train|finetune|pretrain)(?:[_\-.]|$)", stem) or "train" in parts:
        categories.add("train")
    if (
        re.search(r"(?:^|[_-])(?:eval|evaluate|evaluation|benchmark|test)(?:[_\-.]|$)", stem)
        or {"eval", "evaluation", "evaluator", "tests"} & parts
    ):
        categories.add("evaluate")
    if re.search(r"(?:checkpoint|ckpt|weights?)", lower):
        categories.add("checkpoint")
    if re.search(r"(?:package|submission|archive|export|release)", lower):
        categories.add("package")
    return categories


def scan_files(root: Path, limit: int) -> tuple[dict[str, list[str]], int, bool]:
    groups = {
        "documentation": [],
        "configuration": [],
        "train": [],
        "evaluate": [],
        "checkpoint": [],
        "package": [],
    }
    considered = 0
    truncated = False
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if is_secret_like(path):
                continue
            considered += 1
            if considered > limit:
                truncated = True
                return groups, considered - 1, truncated
            relative = path.relative_to(root)
            for category in classify(relative):
                groups[category].append(relative.as_posix())
    for values in groups.values():
        values.sort()
    return groups, considered, truncated


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect a read-only ML repository snapshot.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--mode", choices=MODES, default="orient")
    parser.add_argument("--max-files", type=int, default=5000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = args.path.expanduser().resolve()
    if not target.exists() or not target.is_dir():
        json.dump(
            {"status": "fail", "error": f"Target directory does not exist: {target}"},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 1
    if args.max_files < 1:
        json.dump(
            {"status": "fail", "error": "--max-files must be positive."},
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.stdout.write("\n")
        return 1

    git_root = find_git_root(target)
    root = git_root or target
    groups, considered, truncated = scan_files(root, args.max_files)
    snapshot = {
        "status": "pass",
        "mode": args.mode,
        "target": str(target),
        "repository": {
            "root": str(root),
            "git": git_facts(git_root),
        },
        "instructions": applicable_instructions(root, target),
        "documentation": groups.pop("documentation"),
        "configuration": groups.pop("configuration"),
        "entrypoints": groups,
        "scan": {
            "files_considered": considered,
            "max_files": args.max_files,
            "truncated": truncated,
            "secret_like_names_excluded": True,
        },
    }
    json.dump(snapshot, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
