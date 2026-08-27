import json

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


def test_get_nickname_by_dota_id(tmp_path) -> None:
    repository = PlayerRepository(tmp_path / "name_id.json")
    repository.set("小明", 123)

    assert repository.get_nickname(123) == "小明"
    assert repository.get_nickname(999) is None


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
