# -*- coding: utf-8 -*-
import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

import botpy
from botpy.message import C2CMessage, GroupMessage

from data_center import _log, common_id_path, enable_ai, hero_excel_path
from lib.deepseek_api import deepseek_general
from lib.hero_name_resolver import HeroNameResolver
from lib.open_dota_client import OpenDotaApiClient, OpenDotaApiError
from lib.player_repository import PlayerRepository
from service.hero_win_rate_report import HeroWinRateReportService
from service.qq_command_discovery import QQCommandDiscoveryService
from service.today import TodayReportService

CommandHandler = Callable[[list[str]], str]
PRIVATE_HERO_REPORT_COMMAND = "高胜率英雄"
HERO_REPORT_REPLY_TIMEOUT_SECONDS = 20.0


def normalize_command_content(content: str) -> str:
    """移除 QQ 指令面板自动添加的斜杠前缀。"""
    normalized = content.strip()
    if normalized.startswith(("/", "／")):
        # 同时兼容 QQ 面板的半角斜杠和部分输入法产生的全角斜杠。
        return normalized[1:].lstrip()
    return normalized


@dataclass(frozen=True)
class CommandContext:
    """保存依赖当前 QQ 消息事件的命令参数。"""

    group_openid: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class BotServices:
    """集中声明外部依赖，避免命令解析与网络、存储实现强耦合。"""

    set_dota_id: Callable[[str, int], None]
    get_dota_id: Callable[[str], int | None]
    get_recent_matches: Callable[[int], str | None]
    get_player_wl: Callable[[int, int], tuple[int, int] | None]
    get_today_report: Callable[[], str]
    chat: Callable[[str, str], str]
    resolve_hero_name: Callable[[int], str | None]
    list_player_nicknames: Callable[[], list[str]]


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
    )


class CommandRouter:
    """负责命令解析和分发；SDK 回调只处理异步收发消息。"""

    def __init__(
        self,
        services: BotServices | None = None,
        ai_enabled: bool = enable_ai,
    ):
        self.services = services or build_default_services()
        self.ai_enabled = ai_enabled
        self._commands: dict[str, CommandHandler] = {
            "追踪术": self._track,
            "撒情况": self._recent_matches,
            "今儿": self._today_record,
            "简报": self._report,
        }

    def dispatch(self, content: str, context: CommandContext | None = None) -> str:
        normalized_content = normalize_command_content(content)
        words = normalized_content.split()
        _log.info(f"收到指令: {words}")
        if not words:
            return self._help()

        if words[0].casefold() in {
            "查看当前群openid".casefold(),
            "群openid".casefold(),
        }:
            return self._show_group_openid(words[1:], context)

        handler = self._commands.get(words[0])
        if handler is not None:
            return handler(words[1:])
        conversation_id = (
            context.conversation_id
            if context is not None and context.conversation_id
            else (
                f"group:{context.group_openid}"
                if context is not None and context.group_openid
                else "default"
            )
        )
        return self.chat(content, conversation_id)

    def chat(self, content: str, conversation_id: str) -> str:
        """直接处理 AI 对话，不让私聊内容误入群命令路由。"""
        return (
            self.services.chat(content, conversation_id)
            if self.ai_enabled
            else "听不懂。"
        )

    def _show_group_openid(
        self, args: list[str], context: CommandContext | None
    ) -> str:
        if args:
            return "用法: 查看当前群OpenID"
        if context is None or not context.group_openid:
            return "当前消息不包含群 OpenID。"
        return f"当前群 OpenID：{context.group_openid}"

    def _help(self) -> str:
        players = " ".join(self.services.list_player_nicknames())
        return (
            "\n指令列表:\n"
            "@我 追踪术 昵称 dotaId\n"
            "@我 撒情况 昵称\n"
            "@我 今儿 昵称\n"
            "@我 简报\n"
            "@我 查看当前群OpenID\n"
            "@我 高胜率英雄\n"
            "或者单纯地@我随便聊聊\n"
            f"斗兽场中的选手是: {players}"
        )

    def _track(self, args: list[str]) -> str:
        if not args or args == ["昵称", "dotaId"]:
            # QQ 客户端可能短期缓存旧菜单中的占位参数；占位词不能当作真实输入。
            return "请输入昵称和 dotaId，例如：追踪术 小明 123456789"
        if len(args) != 2:
            return "用法: 追踪术 昵称 dotaId"

        nickname, raw_dota_id = args
        try:
            dota_id = int(raw_dota_id)
        except ValueError:
            return "dotaId 必须是数字。"

        self.services.set_dota_id(nickname, dota_id)
        _log.info(f"追踪术 {nickname} {dota_id}")
        return "哦这个主意好,咱们可以看看这个逼最近打的怎么样~"

    def _recent_matches(self, args: list[str]) -> str:
        nickname, dota_id, error = self._resolve_player(args, "撒情况")
        if error is not None:
            return error

        _log.info(f"撒情况: {nickname}")
        result = self.services.get_recent_matches(dota_id)
        return result or "暂时没有查到近期比赛。"

    def _today_record(self, args: list[str]) -> str:
        nickname, dota_id, error = self._resolve_player(args, "今儿")
        if error is not None:
            return error

        _log.info(f"今儿: {nickname}")
        result = self.services.get_player_wl(dota_id, 1)
        if result is None:
            return "暂时没有查到今日战绩。"
        win, lose = result
        return f"胜:{win}, 败:{lose}"

    def _report(self, args: list[str]) -> str:
        if args:
            return "用法: 简报"
        return self.services.get_today_report()

    def _resolve_player(
        self, args: list[str], command: str
    ) -> tuple[str, int, str | None]:
        if not args or args == ["昵称"]:
            # 兼容尚未刷新的旧菜单 payload，避免实际查询名为“昵称”的选手。
            return "", 0, f"请输入昵称，例如：{command} 小明"
        if len(args) != 1:
            return "", 0, f"用法: {command} 昵称"

        nickname = args[0]
        dota_id = self.services.get_dota_id(nickname)
        if dota_id is None:
            return "", 0, f"还没有追踪选手「{nickname}」。"
        return nickname, dota_id, None


class MyClient(botpy.Client):
    def __init__(
        self,
        *args,
        router: CommandRouter,
        hero_win_rate_report: HeroWinRateReportService | None = None,
        command_discovery: QQCommandDiscoveryService | None = None,
        hero_report_reply_timeout: float = HERO_REPORT_REPLY_TIMEOUT_SECONDS,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.router = router
        self.hero_win_rate_report = hero_win_rate_report or HeroWinRateReportService(
            hero_name_resolver=self.router.services.resolve_hero_name
        )
        self.command_discovery = command_discovery or QQCommandDiscoveryService(
            self.api._http.request
        )
        self.hero_report_reply_timeout = hero_report_reply_timeout
        self._background_report_tasks: set[asyncio.Task[str]] = set()
        self._command_discovery_configured = False

    async def on_ready(self) -> None:
        _log.info(f"robot 「{self.robot.name}」 on_ready!")
        if not self._command_discovery_configured:
            try:
                await self.command_discovery.configure()
                self._command_discovery_configured = True
                _log.info("单聊自定义菜单和群聊指令面板配置成功")
            except Exception:
                # 菜单配置失败不应阻断机器人正常收发消息。
                _log.exception("配置 QQ 自定义菜单或指令面板失败")

    async def on_group_at_message_create(self, message: GroupMessage):
        try:
            normalized_content = normalize_command_content(message.content)
            if normalized_content in {
                PRIVATE_HERO_REPORT_COMMAND,
                "测试英雄胜率榜",
                "测试胜率榜",
            }:
                # 旧指令面板可能短期缓存“测试胜率榜”，统一改为回复当前请求。
                reply = await self._build_hero_report_with_deadline()
            else:
                # 查询接口和 AI SDK 都是同步调用，移出事件循环以免阻塞其他消息。
                context = CommandContext(group_openid=message.group_openid)
                reply = await asyncio.to_thread(
                    self.router.dispatch, message.content, context
                )
        except Exception:
            _log.exception("处理群消息失败")
            reply = "处理失败了，稍后再试。"

        result = await message.reply(
            msg_type=0,
            content=reply,
        )
        # 只记录消息 ID，避免回复正文中的群 OpenID 进入普通运行日志。
        _log.info(f"群消息回复成功: message_id={getattr(result, 'id', None)}")

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        """将 QQ 私聊消息交给 DeepSeek，并使用原消息的 C2C 通道回复。"""
        try:
            normalized_content = normalize_command_content(message.content)
            if normalized_content == PRIVATE_HERO_REPORT_COMMAND:
                # 英雄榜只在用户主动请求时生成，并直接回复当前会话。
                reply = await self._build_hero_report_with_deadline()
            else:
                user_openid = getattr(message.author, "user_openid", None)
                # OpenID 只用于本地隔离上下文，不写日志也不发送给模型。
                conversation_id = f"c2c:{user_openid or message.id}"
                reply = await asyncio.to_thread(
                    self.router.dispatch,
                    message.content,
                    CommandContext(conversation_id=conversation_id),
                )
        except Exception:
            _log.exception("处理私聊消息失败")
            reply = "处理失败了，稍后再试。"

        result = await message.reply(msg_type=0, content=reply)
        _log.info(f"私聊消息回复成功: message_id={getattr(result, 'id', None)}")

    async def _build_hero_report_with_deadline(self) -> str:
        """避免首次回填耗尽 QQ 原消息的可回复时间。"""
        task = asyncio.create_task(asyncio.to_thread(self.hero_win_rate_report.build))
        self._background_report_tasks.add(task)
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=self.hero_report_reply_timeout
            )
            self._background_report_tasks.discard(task)
            return result
        except TimeoutError:
            # 不取消线程，让逐日统计继续写入缓存；用户稍后主动查询即可命中缓存。
            task.add_done_callback(self._finish_background_report)
            return "英雄胜率数据正在更新，请稍后再次查询。"
        except Exception:
            self._background_report_tasks.discard(task)
            raise

    def _finish_background_report(self, task: asyncio.Task[str]) -> None:
        self._background_report_tasks.discard(task)
        try:
            task.result()
        except Exception:
            _log.exception("后台更新英雄胜率数据失败")


def start() -> None:
    """创建并启动 QQ 机器人客户端。"""
    app_id = os.environ.get("QQBOT_APP_ID")
    app_secret = os.environ.get("QQBOT_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("缺少 QQBOT_APP_ID 或 QQBOT_APP_SECRET 环境变量")

    intents = botpy.Intents(public_messages=True)
    client = MyClient(
        intents=intents,
        router=CommandRouter(),
    )
    client.run(appid=app_id, secret=app_secret)


if __name__ == "__main__":
    start()
