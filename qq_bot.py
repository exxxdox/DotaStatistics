# -*- coding: utf-8 -*-
import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

import botpy
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from botpy.message import C2CMessage, GroupMessage

from data_center import _log, enable_ai, name_id_ref
from lib.deepseekapi import deepseekGeneral
from lib.open_dota_api import getPlayerWL, getRecentMatchesApi
from lib.utils import SetDotaId, getDotaId, getHeroName
from service.qq_command_discovery import QQCommandDiscoveryService
from service.today import todayAnalyze
from service.weekly_hero_report import HeroWinRateReportService

CommandHandler = Callable[[list[str]], str]
PRIVATE_HERO_REPORT_COMMAND = "高胜率英雄"


@dataclass(frozen=True)
class CommandContext:
    """保存依赖当前 QQ 消息事件的命令参数。"""

    group_openid: str | None = None
    conversation_id: str | None = None


@dataclass(frozen=True)
class BotServices:
    """集中声明外部依赖，避免命令解析与网络、存储实现强耦合。"""

    set_dota_id: Callable[[str, int], None] = SetDotaId
    get_dota_id: Callable[[str], int | None] = getDotaId
    get_recent_matches: Callable[[int], str | None] = getRecentMatchesApi
    get_player_wl: Callable[[int, int], tuple[int, int] | None] = getPlayerWL
    get_today_report: Callable[[], str] = todayAnalyze
    chat: Callable[[str, str], str] = deepseekGeneral


class CommandRouter:
    """负责命令解析和分发；SDK 回调只处理异步收发消息。"""

    def __init__(
        self,
        services: BotServices | None = None,
        ai_enabled: bool = enable_ai,
    ):
        self.services = services or BotServices()
        self.ai_enabled = ai_enabled
        self._commands: dict[str, CommandHandler] = {
            "追踪术": self._track,
            "撒情况": self._recent_matches,
            "今儿": self._today_record,
            "简报": self._report,
        }

    def dispatch(self, content: str, context: CommandContext | None = None) -> str:
        words = content.split()
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
        players = " ".join(nickname for target in name_id_ref for nickname in target)
        return (
            "\n指令列表:\n"
            "@我 追踪术 昵称 dotaId\n"
            "@我 撒情况 昵称\n"
            "@我 今儿 昵称\n"
            "@我 简报\n"
            "@我 查看当前群OpenID\n"
            "@我 测试英雄胜率榜\n"
            "或者单纯地@我随便聊聊\n"
            f"斗兽场中的选手是: {players}"
        )

    def _track(self, args: list[str]) -> str:
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
        report_group_openid: str | None = None,
        hero_win_rate_report: HeroWinRateReportService | None = None,
        command_discovery: QQCommandDiscoveryService | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.router = router
        self.report_group_openid = report_group_openid
        self.hero_win_rate_report = hero_win_rate_report or HeroWinRateReportService(
            hero_name_resolver=getHeroName
        )
        self.command_discovery = command_discovery or QQCommandDiscoveryService(
            self.api._http.request
        )
        self._command_discovery_configured = False
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        # 固定任务 ID 配合 replace_existing，避免网关重连后重复发送。
        self.scheduler.add_job(
            self._send_hero_win_rate_report,
            trigger="cron",
            hour=20,
            minute=0,
            id="hero-win-rate-report",
            replace_existing=True,
        )

    async def on_ready(self) -> None:
        _log.info(f"robot 「{self.robot.name}」 on_ready!")
        if not self._command_discovery_configured:
            try:
                await self.command_discovery.configure()
                self._command_discovery_configured = True
                _log.info("单聊自定义菜单和群聊指令面板配置成功")
            except Exception:
                # 菜单配置失败不应阻断机器人收发消息和定时任务。
                _log.exception("配置 QQ 自定义菜单或指令面板失败")
        if not self.report_group_openid:
            _log.warning("未配置 QQBOT_GROUP_OPENID，晚八点英雄胜率榜不会发送")
        elif not self.scheduler.running:
            self.scheduler.start()
            _log.info("已启动每日 20:00 英雄胜率榜任务")

    async def on_group_at_message_create(self, message: GroupMessage):
        try:
            if message.content.strip() in {"测试英雄胜率榜", "测试胜率榜"}:
                # 手动测试复用定时任务的主动发送方法和目标群，不走当前消息回复通道。
                sent = await self._send_hero_win_rate_report()
                reply = (
                    "测试榜单已发送到定时任务目标群。"
                    if sent
                    else "测试榜单发送失败，请检查配置和服务日志。"
                )
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
            if message.content.strip() == PRIVATE_HERO_REPORT_COMMAND:
                # 私聊榜单直接回复请求人，不复用定时任务的目标群发送通道。
                reply = await asyncio.to_thread(self.hero_win_rate_report.build)
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

    async def _send_hero_win_rate_report(self) -> bool:
        if not self.report_group_openid:
            _log.warning("未配置 QQBOT_GROUP_OPENID，无法发送英雄胜率榜")
            return False
        try:
            # OpenDota Explorer 是同步 HTTP 请求，避免阻塞 QQ SDK 的事件循环。
            content = await asyncio.to_thread(self.hero_win_rate_report.build)
            result = await self.api.post_group_message(
                group_openid=self.report_group_openid,
                msg_type=0,
                content=content,
            )
            _log.info(
                f"英雄胜率榜发送成功: message_id={getattr(result, 'id', None)}"
            )
            return True
        except Exception:
            _log.exception("英雄胜率榜生成或发送失败")
            return False

    async def close(self) -> None:
        if self.scheduler.running:
            # shutdown 本身不是协程；不等待长任务，保证机器人可以快速退出。
            self.scheduler.shutdown(wait=False)
        await super().close()


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
        report_group_openid=os.environ.get("QQBOT_GROUP_OPENID"),
    )
    client.run(appid=app_id, secret=app_secret)


if __name__ == "__main__":
    start()
