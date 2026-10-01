from pathlib import Path

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


def test_data_dir_accepts_container_volume_path(monkeypatch, tmp_path: Path) -> None:
    configured_dir = tmp_path / "persistent-data"
    monkeypatch.setenv("DATA_DIR", str(configured_dir))

    assert data_center._resolve_data_dir() == configured_dir.resolve()
