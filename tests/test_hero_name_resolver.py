import json
from pathlib import Path

import pytest

from lib.hero_name_resolver import HeroNameResolver


def _write_mapping(tmp_path: Path) -> Path:
    path = tmp_path / "hero_name.json"
    path.write_text(json.dumps({1: "敌法师", 2: "斧王"}, ensure_ascii=False), encoding="utf-8")
    return path


def test_resolve_zh_name_from_json(tmp_path) -> None:
    resolver = HeroNameResolver(_write_mapping(tmp_path))

    resolver.load()

    assert resolver.resolve(1) == "敌法师"
    assert resolver.resolve(2) == "斧王"


def test_resolve_falls_back_to_en_name_when_zh_missing(tmp_path) -> None:
    resolver = HeroNameResolver(_write_mapping(tmp_path))
    resolver.load()
    resolver.set_en_names({1: "Anti-Mage", 2: "Axe"})

    assert resolver.resolve(3) is None
    assert resolver.resolve(1) == "敌法师"


def test_set_en_names_supplies_fallback_for_unknown_zh(tmp_path) -> None:
    resolver = HeroNameResolver(_write_mapping(tmp_path))
    resolver.load()
    resolver.set_en_names({99: "Techies"})

    assert resolver.resolve(99) == "Techies"


def test_resolve_unknown_hero_returns_none(tmp_path) -> None:
    resolver = HeroNameResolver(_write_mapping(tmp_path))
    resolver.load()

    assert resolver.resolve(999) is None


@pytest.mark.parametrize("data", [[], {}, {"1": None}, {"1": ""}, {"bad": "敌法师"}])
def test_invalid_mapping_is_rejected(tmp_path, data) -> None:
    path = tmp_path / "heroes.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        HeroNameResolver(path).load()


def test_static_mapping_contains_all_migrated_names() -> None:
    resolver = HeroNameResolver(Path(__file__).resolve().parents[1] / "res/hero_name.json")
    resolver.load()
    assert len(resolver._zh_names) == 124
    assert resolver.resolve(1)
