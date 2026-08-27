import pandas as pd

from lib.hero_name_resolver import HeroNameResolver


def _write_excel(tmp_path) -> None:
    path = tmp_path / "hero_name.xlsx"
    pd.DataFrame(
        [{"id": 1, "name_zh": "敌法师"}, {"id": 2, "name_zh": "斧王"}]
    ).to_excel(path, index=False)
    return path


def test_resolve_zh_name_from_excel(tmp_path) -> None:
    resolver = HeroNameResolver(_write_excel(tmp_path))

    resolver.load()

    assert resolver.resolve(1) == "敌法师"
    assert resolver.resolve(2) == "斧王"


def test_resolve_falls_back_to_en_name_when_zh_missing(tmp_path) -> None:
    resolver = HeroNameResolver(_write_excel(tmp_path))
    resolver.load()
    resolver.set_en_names({1: "Anti-Mage", 2: "Axe"})

    assert resolver.resolve(3) is None
    assert resolver.resolve(1) == "敌法师"


def test_set_en_names_supplies_fallback_for_unknown_zh(tmp_path) -> None:
    resolver = HeroNameResolver(_write_excel(tmp_path))
    resolver.load()
    resolver.set_en_names({99: "Techies"})

    assert resolver.resolve(99) == "Techies"


def test_resolve_unknown_hero_returns_none(tmp_path) -> None:
    resolver = HeroNameResolver(_write_excel(tmp_path))
    resolver.load()

    assert resolver.resolve(999) is None
