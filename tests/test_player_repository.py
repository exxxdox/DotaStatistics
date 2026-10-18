import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from lib.player_repository import PlayerRepository


def _write_records(tmp_path, records) -> None:
    path = tmp_path / "name_id.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def test_set_adds_new_player(tmp_path) -> None:
    path = tmp_path / "name_id.json"
    repository = PlayerRepository(path)

    repository.set("小明", 123)

    assert repository.get("小明") == 123
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"nick_name": "小明", "dota_id": 123}
    ]


def test_set_updates_existing_player_in_memory_and_on_disk(tmp_path) -> None:
    path = tmp_path / "name_id.json"
    repository = PlayerRepository(path)
    repository.set("小明", 123)

    repository.set("小明", 456)

    assert repository.get("小明") == 456
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"nick_name": "小明", "dota_id": 456}
    ]


def test_get_returns_none_for_unknown_nickname(tmp_path) -> None:
    repository = PlayerRepository(tmp_path / "name_id.json")

    assert repository.get("陌生人") is None


def test_persists_players_and_reloads(tmp_path) -> None:
    path = tmp_path / "name_id.json"
    repository = PlayerRepository(path)
    repository.set("小明", 123)
    repository.set("小红", 456)

    reloaded = PlayerRepository(path)

    assert reloaded.get("小明") == 123
    assert reloaded.get("小红") == 456


def test_nicknames_lists_players_in_insertion_order(tmp_path) -> None:
    path = _write_records(
        tmp_path,
        [
            {"nick_name": "小明", "dota_id": 123},
            {"nick_name": "小红", "dota_id": 456},
        ],
    )
    repository = PlayerRepository(path)

    assert repository.nicknames() == ["小明", "小红"]


def test_missing_file_starts_empty_and_creates_on_write(tmp_path) -> None:
    path = tmp_path / "name_id.json"
    repository = PlayerRepository(path)

    assert repository.nicknames() == []
    repository.set("小明", 123)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"nick_name": "小明", "dota_id": 123}
    ]


def test_bindings_returns_independent_current_and_persistent_snapshot(tmp_path) -> None:
    path = tmp_path / "name_id.json"
    repository = PlayerRepository(path)
    assert repository.bindings() == {}
    repository.set("小明", 123)
    snapshot = repository.bindings()
    snapshot["小明"] = 999
    snapshot["小红"] = 456

    assert repository.bindings() == {"小明": 123}
    repository.set("小明", 789)
    assert repository.bindings() == {"小明": 789}
    assert PlayerRepository(path).bindings() == {"小明": 789}


def test_bindings_preserves_first_duplicate_nickname(tmp_path) -> None:
    path = _write_records(
        tmp_path,
        [
            {"nick_name": "小明", "dota_id": 123},
            {"nick_name": "小明", "dota_id": 456},
            {"nick_name": "小红", "dota_id": 789},
        ],
    )
    repository = PlayerRepository(path)

    assert repository.bindings() == {"小明": repository.get("小明"), "小红": 789}
    assert repository.bindings()["小明"] == 123


@pytest.mark.parametrize("content", ["{broken", '{"nick_name": "小明"}'])
def test_invalid_file_is_rejected_without_overwrite(tmp_path, content) -> None:
    path = tmp_path / "name_id.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises((json.JSONDecodeError, ValueError)):
        PlayerRepository(path)

    assert path.read_text(encoding="utf-8") == content


def test_concurrent_updates_are_all_persisted(tmp_path) -> None:
    path = tmp_path / "name_id.json"
    repository = PlayerRepository(path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda index: repository.set(f"player-{index}", index), range(40)))

    assert PlayerRepository(path).bindings() == {
        f"player-{index}": index for index in range(40)
    }


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions only")
def test_storage_permissions_are_private(tmp_path) -> None:
    parent = tmp_path / "players"
    path = parent / "name_id.json"
    PlayerRepository(path).set("小明", 123)

    assert parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
