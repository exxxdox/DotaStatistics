from unittest.mock import Mock

import bootstrap
import qq_bot
from service.command_router import BotServices, CommandContext, CommandRouter


def test_default_router_assembles_services_without_live_integrations(monkeypatch) -> None:
    # 启动装配不能调用可选网络接口，避免 OpenDota 故障阻塞 QQ 上线。
    players = Mock()
    players.get.return_value = 123
    hero_names = Mock()
    api_client = Mock()
    api_client.get_recent_matches.return_value = "比赛:123"
    today_report = Mock()
    today_report.build.return_value = "今日简报"
    agent_tools = Mock()
    agent_tools.definitions = [{"type": "function"}]
    monkeypatch.setattr(bootstrap, "PlayerRepository", Mock(return_value=players))
    monkeypatch.setattr(bootstrap, "HeroNameResolver", Mock(return_value=hero_names))
    monkeypatch.setattr(bootstrap, "OpenDotaApiClient", Mock(return_value=api_client))
    monkeypatch.setattr(bootstrap, "TodayReportService", Mock(return_value=today_report))
    monkeypatch.setattr(bootstrap, "DotaAgentTools", Mock(return_value=agent_tools))

    memory = Mock()
    router, hero_report, assembled_memory = bootstrap.build_application(memory_store=memory)
    assert assembled_memory is memory
    assert router.memory_store is memory
    assert hero_report.api_client is api_client
    assert router.services.chat.keywords["memory_store"] is memory

    assert router.dispatch("撒情况 小明") == "比赛:123"
    api_client.get_recent_matches.assert_called_once_with(123)
    assert router.dispatch("简报") == "今日简报"
    assert router.services.chat.func is bootstrap.deepseek_general
    assert router.services.chat.keywords["player_bindings"] == players.bindings
    assert router.services.chat.keywords["tool_definitions"] == agent_tools.definitions
    assert router.services.chat.keywords["execute_tool"] == agent_tools.execute
    assert router.services.ask_command_parameter.func is bootstrap.deepseek_command_question
    assert router.services.ask_command_parameter.keywords["player_bindings"] == players.bindings
    assert router.services.list_player_bindings == players.bindings
    bootstrap.OpenDotaApiClient.assert_called_once_with(
        hero_name_resolver=hero_names.resolve
    )
    bootstrap.TodayReportService.assert_called_once_with(
        players=players, api_client=api_client
    )
    bootstrap.DotaAgentTools.assert_called_once_with(players, api_client)
    hero_names.load.assert_called_once_with()
    api_client.get_heroes.assert_not_called()
    hero_names.set_en_names.assert_not_called()


def test_legacy_imports_keep_the_same_objects() -> None:
    assert qq_bot.BotServices is BotServices
    assert qq_bot.CommandContext is CommandContext
    assert qq_bot.CommandRouter is CommandRouter
    assert qq_bot.build_application is bootstrap.build_application
