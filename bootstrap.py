"""集中组装真实依赖，避免命令路由和 QQ 收发层各自维护初始化逻辑。"""

from data_center import _log, common_id_path, hero_excel_path
from lib.deepseek_api import deepseek_command_question, deepseek_general
from lib.hero_name_resolver import HeroNameResolver
from lib.open_dota_client import OpenDotaApiClient, OpenDotaApiError
from lib.player_repository import PlayerRepository
from service.command_router import BotServices
from service.today import TodayReportService


def build_default_services() -> BotServices:
    """组装依赖真实实现的 BotServices，供 CommandRouter 默认使用。"""
    players = PlayerRepository(common_id_path)
    hero_names = HeroNameResolver(hero_excel_path)
    hero_names.load()
    api_client = OpenDotaApiClient(hero_name_resolver=hero_names.resolve)
    try:
        hero_names.set_en_names(api_client.get_heroes())
    except OpenDotaApiError:
        # 网络失败不阻断启动，中文名已足够覆盖常见英雄。
        _log.warning("获取 OpenDota 英雄英文名失败，使用中文名后备")
    today_report = TodayReportService(players=players, api_client=api_client)
    return BotServices(
        set_dota_id=players.set,
        get_dota_id=players.get,
        get_recent_matches=api_client.get_recent_matches,
        get_player_wl=api_client.get_player_wl,
        get_today_report=today_report.build,
        chat=deepseek_general,
        resolve_hero_name=hero_names.resolve,
        list_player_nicknames=players.nicknames,
        ask_command_parameter=deepseek_command_question,
    )
