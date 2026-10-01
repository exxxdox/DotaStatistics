"""QQ SDK 适配层：处理事件、异步调用与当前会话回复。"""

import asyncio
import os
from datetime import date, datetime, timezone

import botpy
from botpy.message import C2CMessage, GroupMessage

from data_center import _log
from lib.conversation_memory import ConversationMemory, get_memory_store
# 保留旧模块的公开导入路径，避免部署扩展和已有调用方随职责拆分失效。
from bootstrap import build_default_services
from service.command_router import (
    BotServices,
    CommandContext,
    CommandHandler,
    CommandRouter,
    normalize_command_content,
)
from service.hero_win_rate_report import HeroWinRateReportService
from service.qq_command_discovery import QQCommandDiscoveryService

PRIVATE_HERO_REPORT_COMMAND = "高胜率英雄"
HERO_REPORT_REPLY_TIMEOUT_SECONDS = 20.0
HERO_REPORT_CACHE_SECONDS = 300.0


class MyClient(botpy.Client):
    def __init__(
        self,
        *args,
        router: CommandRouter,
        hero_win_rate_report: HeroWinRateReportService | None = None,
        command_discovery: QQCommandDiscoveryService | None = None,
        hero_report_reply_timeout: float = HERO_REPORT_REPLY_TIMEOUT_SECONDS,
        memory_store: ConversationMemory | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.router = router
        self.memory_store = memory_store or get_memory_store()
        # 收发记录和追问状态使用同一持久目录，测试或部署注入时也保持一致。
        self.router.memory_store = self.memory_store
        self.hero_win_rate_report = hero_win_rate_report or HeroWinRateReportService(
            hero_name_resolver=self.router.services.resolve_hero_name
        )
        self.command_discovery = command_discovery or QQCommandDiscoveryService(
            self.api._http.request
        )
        self.hero_report_reply_timeout = hero_report_reply_timeout
        # 单个任务引用同时保活后台查询和保留缓存，无需另一份任务集合。
        self._hero_report_task: asyncio.Task[str] | None = None
        self._hero_report_completed_at = 0.0
        self._hero_report_day: date | None = None
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

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        author = getattr(message, "author", None)
        await self._handle_message(
            message,
            # 缺失群标识时由存储边界拒绝，不能把异常事件合并到 group:None。
            f"group:{message.group_openid or ''}",
            getattr(author, "member_openid", None),
            is_group=True,
        )

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        # 长期记忆必须使用稳定用户标识；不能退回消息 ID 或共享 default 会话。
        user_openid = getattr(message.author, "user_openid", None)
        await self._handle_message(message, f"c2c:{user_openid or ''}", user_openid)

    async def _handle_message(
        self,
        message: GroupMessage | C2CMessage,
        conversation_id: str,
        speaker_id: str | None,
        *,
        is_group: bool = False,
    ) -> None:
        try:
            # 先落盘再执行耗时业务，命令、AI 失败和超时提示也走同一记录入口。
            record_id = await asyncio.to_thread(
                self.memory_store.append,
                conversation_id, "user", message.content, speaker_id,
            )
        except Exception:
            _log.exception("保存用户对话失败")
            await message.reply(msg_type=0, content="对话记录存储失败，请稍后再试。")
            return

        try:
            normalized_content = normalize_command_content(message.content)
            hero_commands = {PRIVATE_HERO_REPORT_COMMAND}
            if is_group:
                # 兼容 QQ 尚未刷新的旧指令面板。
                hero_commands.update({"测试英雄胜率榜", "测试胜率榜"})
            if normalized_content in hero_commands:
                if speaker_id:
                    await asyncio.to_thread(
                        self.memory_store.set_pending, conversation_id, speaker_id, None
                    )
                reply = await self._build_hero_report_with_deadline()
            else:
                context = CommandContext(
                    group_openid=message.group_openid if is_group else None,
                    conversation_id=conversation_id,
                    history_before_id=record_id,
                    speaker_id=speaker_id,
                )
                # 同步网络、压缩与数据库操作均移出事件循环。
                reply = await asyncio.to_thread(
                    self.router.dispatch, message.content, context
                )
        except Exception:
            _log.exception("处理对话失败")
            reply = "处理失败了，稍后再试。"

        try:
            # 发送前保存生成的回复，发送失败仍保留此次输入与回复尝试。
            await asyncio.to_thread(
                self.memory_store.append, conversation_id, "assistant", reply
            )
        except Exception:
            _log.exception("保存机器人回复失败")
            await message.reply(msg_type=0, content="对话记录存储失败，请稍后再试。")
            return

        result = await message.reply(msg_type=0, content=reply)
        # 只记消息 ID，用户标识和对话正文不进入普通运行日志。
        _log.info(f"消息回复成功: message_id={getattr(result, 'id', None)}")

    async def _build_hero_report_with_deadline(self) -> str:
        """复用统计刷新与近期结果，避免重复查询启动另一批上游请求。"""
        loop = asyncio.get_running_loop()
        today = datetime.now(timezone.utc).date()
        task = self._hero_report_task
        if task is None or (task.done() and (
            task.cancelled() or task.exception() is not None
            or self._hero_report_day != today
            or loop.time() - self._hero_report_completed_at >= HERO_REPORT_CACHE_SECONDS
        )):
            task = asyncio.create_task(asyncio.to_thread(self.hero_win_rate_report.build))
            self._hero_report_task = task
            self._hero_report_day = today
            task.add_done_callback(self._finish_background_report)
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), timeout=self.hero_report_reply_timeout
            )
        except TimeoutError:
            # 保留同一任务继续逐日落盘，重试不会启动另一批查询。
            return "英雄胜率数据正在更新，请稍后再次查询。"

    def _finish_background_report(self, task: asyncio.Task[str]) -> None:
        if task is self._hero_report_task:
            # 完成的 Task 保留完整报表，随后请求可立即复用；跨 UTC 日会失效。
            self._hero_report_completed_at = asyncio.get_running_loop().time()
        if task.cancelled():
            return
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
        # 容器日志交给 stdout/stderr 和 Docker 收集，避免 SDK 在应用目录写日志文件。
        ext_handlers=False,
    )
    client.run(appid=app_id, secret=app_secret)


if __name__ == "__main__":
    start()
