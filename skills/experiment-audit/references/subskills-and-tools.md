# Subskills and Tools

| Need | Preferred capability |
| --- | --- |
| Systematic root-cause diagnosis | `superpowers:systematic-debugging` |
| Test-first implementation after explicit authorization | `superpowers:test-driven-development` |
| Current framework or SDK behavior | current official documentation capability |
| GitHub repository metadata | GitHub connector or local Git |
| PyTorch Lightning project | `pytorch-lightning` |
| Stable Baselines project | `stable-baselines3` |
| scikit-learn project | `scikit-learn` |
| End-to-end timing and GPU/host memory peaks | [profiling workflow](profiling.md), then only the profiler needed for the active framework |

## Selection Rules

1. Read repository-local instructions before any domain skill.
2. Use current official docs for libraries and services.
3. Use local source and config for repository behavior.
4. Use systematic debugging for diagnosis; do not implement a fix unless requested.
5. Use test-driven development for new behavior or bug fixes.
6. Do not invoke a training framework skill merely because the dependency is present; confirm it is on the active execution path.
