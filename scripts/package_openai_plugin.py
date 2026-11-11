"""Package the portable plugin and its skills for ChatGPT ZIP import."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


EXCLUDED_DIRECTORIES = {
    "__pycache__",
    ".pytest_cache",
    ".research",
    ".venv",
    ".git",
    ".remember",
}


def package_plugin(root: Path, output: Path) -> int:
    """Write the plugin payload at the ZIP root, excluding local session data."""
    root = root.resolve()
    skills = sorted((root / "skills").glob("*/SKILL.md"))
    if not skills:
        raise ValueError("No skills found under skills/<name>/SKILL.md")

    files = [root / "plugin.json", root / "README.md"]
    files.extend(
        path
        for path in sorted((root / "skills").rglob("*"))
        if path.is_file()
        and path.name != ".DS_Store"
        and not EXCLUDED_DIRECTORIES.intersection(path.relative_to(root).parts)
        and path.suffix not in {".pyc", ".pyo", ".log"}
        and path.name != ".env"
        and not path.name.startswith(".env.")
    )
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Plugin payload must stay inside its root: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    return len(skills)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    output = root / "dist" / f"{manifest['name']}-{manifest['version']}.zip"
    count = package_plugin(root, output)
    print(f"Packaged {count} skills: {output}")


if __name__ == "__main__":
    main()
