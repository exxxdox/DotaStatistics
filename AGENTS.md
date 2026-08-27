# Repository Guidelines

CLAUDE.md与AGENTS.md应保持一致

## 数据来源api文档

https://docs.opendota.com

## Project Structure & Module Organization

`main.py` is the application entry point, while `qq_bot.py` contains the QQ SDK client, command routing, and dependency assembly (`build_default_services`). Shared config and resource paths live in `data_center.py`; player and hero mappings are owned by repository classes in `lib/` (`player_repository.py`, `hero_name_resolver.py`) rather than global state. Put external integrations in `lib/` (`open_dota_client.py`, `deepseek_api.py`) and higher-level report workflows in `service/`. Static and deployment assets belong in `res/`; generated `res/name_id.json` is intentionally ignored. Tests live in `tests/` and should mirror the module under test, for example `tests/test_qq_bot.py`.

## Build, Test, and Development Commands

- `uv sync` creates `.venv` and installs locked development dependencies.
- `uv run python main.py` starts the bot locally using environment configuration.
- `uv run pytest` runs the complete test suite configured by `pyproject.toml`.
- `uv run pytest tests/test_qq_bot.py -q` runs the command-router tests only.
- `./startup.sh` starts the locked production environment on Linux; `sudo ./init.sh` installs and enables the systemd service.

Use Python 3.12. Commit `uv.lock` whenever dependency changes alter the resolved environment.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation. Use `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Add type hints to new or changed public functions. Keep command parsing separate from network, filesystem, and SDK operations; inject those dependencies through `BotServices` so behavior remains testable. Comments should explain why a non-obvious decision exists, not restate the code. No formatter or linter is currently configured, so keep imports grouped and changes consistent with nearby code.

## Testing Guidelines

Pytest discovers `tests/test_*.py` and functions named `test_*`. Add focused tests for every command branch and regression. Replace OpenDota, DeepSeek, QQ SDK, and file access with in-memory fakes or mocks; tests must not require credentials or live network access. There is no enforced coverage threshold, but changed routing and error paths should be covered.

## Commit & Pull Request Guidelines

Existing history uses short messages such as `update`; new commits should be clearer, imperative, and in English, for example `fix: validate tracked Dota ID`. Keep each commit focused. Pull requests should explain user-visible behavior, list verification commands, link relevant issues, and include sample bot input/output when responses change.

## Security & Configuration

Never commit `.env`, API keys, or generated player data. Configure `QQBOT_APP_ID`, `QQBOT_APP_SECRET`, and `DEEPSEEK_API_KEY` locally; production reads them from `/root/.secrets`.
