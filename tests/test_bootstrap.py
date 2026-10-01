from unittest.mock import Mock

import pytest

import bootstrap
import qq_bot
from lib.open_dota_client import OpenDotaApiError
from service.command_router import BotServices, CommandContext, CommandRouter


@pytest.mark.parametrize("heroes_available", [True, False])
def test_default_router_assembles_services_without_live_integrations(
    monkeypatch, heroes_available: bool
) -> None:
    # 同时覆盖默认依赖组装和英文名加载失败，防止拆分后启动路径悄悄失效。
    players = Mock()
    players.get.return_value = 123
    hero_names = Mock()
    api_client = Mock()
    api_client.get_recent_matches.return_value = "比赛:123"
    if not heroes_available:
        api_client.get_heroes.side_effect = OpenDotaApiError("unavailable")
    today_report = Mock()
    today_report.build.return_value = "今日简报"
    monkeypatch.setattr(bootstrap, "PlayerRepository", Mock(return_value=players))
    monkeypatch.setattr(bootstrap, "HeroNameResolver", Mock(return_value=hero_names))
    monkeypatch.setattr(bootstrap, "OpenDotaApiClient", Mock(return_value=api_client))
    monkeypatch.setattr(bootstrap, "TodayReportService", Mock(return_value=today_report))

    router = CommandRouter()

    assert router.dispatch("撒情况 小明") == "比赛:123"
    assert router.dispatch("简报") == "今日简报"
    assert router.services.chat.func is bootstrap.deepseek_general
    assert router.services.chat.keywords["player_bindings"] == players.bindings
    assert router.services.ask_command_parameter.func is bootstrap.deepseek_command_question
    assert router.services.ask_command_parameter.keywords["player_bindings"] == players.bindings
    assert router.services.list_player_bindings == players.bindings
    bootstrap.OpenDotaApiClient.assert_called_once_with(
        hero_name_resolver=hero_names.resolve
    )
    bootstrap.TodayReportService.assert_called_once_with(
        players=players, api_client=api_client
    )
    hero_names.load.assert_called_once_with()
    if heroes_available:
        hero_names.set_en_names.assert_called_once_with(api_client.get_heroes.return_value)
    else:
        hero_names.set_en_names.assert_not_called()


def test_legacy_imports_keep_the_same_objects() -> None:
    assert qq_bot.BotServices is BotServices
    assert qq_bot.CommandContext is CommandContext
    assert qq_bot.CommandRouter is CommandRouter
    assert qq_bot.build_default_services is bootstrap.build_default_services
