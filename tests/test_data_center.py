from pathlib import Path

import pytest

import data_center


def test_data_dir_defaults_to_project_data_directory(monkeypatch) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)

    assert data_center._resolve_data_dir() == data_center.project_dir / "data"
    assert data_center.hero_names_path == data_center.resource_dir / "hero_name.json"
    assert data_center.common_id_path == data_center.data_dir / "name_id.json"
    assert (
        data_center.hero_stats_cache_path
        == data_center.data_dir / "daily_hero_stats_cache.json"
    )


def test_operator_ids_require_explicit_configuration(monkeypatch) -> None:
    monkeypatch.delenv("DOTA_OPERATOR_IDS", raising=False)
    assert data_center._resolve_operator_ids() == frozenset()

    monkeypatch.setenv("DOTA_OPERATOR_IDS", " op-a, ,op-b ")
    assert data_center._resolve_operator_ids() == frozenset({"op-a", "op-b"})


def test_data_dir_accepts_container_volume_path(monkeypatch, tmp_path: Path) -> None:
    configured_dir = tmp_path / "persistent-data"
    monkeypatch.setenv("DATA_DIR", str(configured_dir))

    assert data_center._resolve_data_dir() == configured_dir.resolve()


@pytest.mark.parametrize("configured, expected", [(None, 4), ("1", 1), (" 8 ", 8), ("32", 32)])
def test_command_workers_configuration(monkeypatch, configured, expected) -> None:
    if configured is None:
        monkeypatch.delenv("DOTA_COMMAND_WORKERS", raising=False)
    else:
        monkeypatch.setenv("DOTA_COMMAND_WORKERS", configured)
    assert data_center._resolve_command_workers() == expected


@pytest.mark.parametrize("configured", ["", "0", "-1", "33", "1.5", "unlimited"])
def test_command_workers_rejects_invalid_configuration(monkeypatch, configured) -> None:
    monkeypatch.setenv("DOTA_COMMAND_WORKERS", configured)
    with pytest.raises(ValueError, match="DOTA_COMMAND_WORKERS"):
        data_center._resolve_command_workers()


@pytest.mark.parametrize("configured, expected", [(None, 2), ("1", 1), (" 4 ", 4), ("16", 16)])
def test_ai_workers_configuration(monkeypatch, configured, expected) -> None:
    if configured is None:
        monkeypatch.delenv("DOTA_AI_WORKERS", raising=False)
    else:
        monkeypatch.setenv("DOTA_AI_WORKERS", configured)
    assert data_center._resolve_ai_workers() == expected


@pytest.mark.parametrize("configured", ["", "0", "-1", "17", "1.5"])
def test_ai_workers_rejects_invalid_configuration(monkeypatch, configured) -> None:
    monkeypatch.setenv("DOTA_AI_WORKERS", configured)
    with pytest.raises(ValueError, match="DOTA_AI_WORKERS"):
        data_center._resolve_ai_workers()
