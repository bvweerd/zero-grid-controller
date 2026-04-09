---
paths:
  - "custom_components/**/*.py"
  - "tests/**/*.py"
---

# Code Style

- Formatter/linter: **Ruff** (not black/isort)
- Line length: 88 (E501 ignored — no hard wrap enforcement)
- Ruff rules enabled: E, F, W, I, UP, B, C4, SIM
- Target Python version: 3.13
- Type hints required on **all** public functions (mypy strict mode)
- No synchronous I/O in async context
- Use `_LOGGER = logging.getLogger(__name__)` in every module
- Use `async def` for all I/O and platform setup functions
- Follow HA entity class hierarchy (Entity → SensorEntity, SwitchEntity, etc.)
- Config flow: use `config_entries.ConfigFlow` as base; subentries via `SubentryFlowHandler`
