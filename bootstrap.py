"""集中组装真实依赖，避免命令路由和 QQ 收发层各自维护初始化逻辑。"""

from functools import partial

from data_center import common_id_path, hero_names_path
from lib.conversation_memory import ConversationMemory, get_memory_store
from lib.deepseek_api import deepseek_command_question, deepseek_general
from lib.hero_name_resolver import HeroNameResolver
from lib.open_dota_client import OpenDotaApiClient
from lib.player_repository import PlayerRepository
from service.command_router import BotServices, CommandRouter
from service.dota_agent import DotaAgentTools
from service.hero_win_rate_report import HeroWinRateReportService
from service.today import TodayReportService


def build_application(
    memory_store: ConversationMemory | None = None,
) -> tuple[CommandRouter, HeroWinRateReportService, ConversationMemory]:
    """一次组装业务依赖，所有入口共用同一存储和 OpenDota 客户端。"""
    memory_store = memory_store or get_memory_store()
    players = PlayerRepository(common_id_path)
    hero_names = HeroNameResolver(hero_names_path)
    hero_names.load()
    api_client = OpenDotaApiClient(hero_name_resolver=hero_names.resolve)
    # 启动只依赖本地名称表，避免可选的 OpenDota 元数据阻塞 QQ 上线。
    today_report = TodayReportService(players=players, api_client=api_client)
    hero_report = HeroWinRateReportService(
        api_client=api_client, hero_name_resolver=hero_names.resolve
    )
    agent_tools = DotaAgentTools(players, api_client)
    services = BotServices(
        set_dota_id=players.set,
        get_dota_id=players.get,
        get_recent_matches=api_client.get_recent_matches,
        get_today_report=today_report.build,
        # 绑定读取函数而非启动时的名单，既复用持久资料，又避免AI使用过期ID。
        chat=partial(
            deepseek_general,
            player_bindings=players.bindings,
            memory_store=memory_store,
            tool_definitions=agent_tools.definitions,
            execute_tool=agent_tools.execute,
        ),
        list_player_nicknames=players.nicknames,
        ask_command_parameter=partial(deepseek_command_question, player_bindings=players.bindings),
        list_player_bindings=players.bindings,
    )
    return (
        CommandRouter(services, memory_store=memory_store),
        hero_report,
        memory_store,
    )
