"""QQ SDK 适配层：处理事件、异步调用与当前会话回复。"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, timezone
from threading import BoundedSemaphore
from weakref import WeakValueDictionary

import botpy
from botpy.message import C2CMessage, GroupMessage

from data_center import _log, command_workers
from lib.conversation_memory import ConversationMemory
from lib.qq_sdk_security import configure_qq_sdk_security
# 保留旧模块的公开导入路径，避免部署扩展和已有调用方随职责拆分失效。
from bootstrap import build_application
from service.command_router import (
    BotServices,
    CommandContext,
    CommandHandler,
    CommandRouter,
    normalize_command_content,
)
from service.commands import CLEAR_COMMAND, HERO_COMMAND
from service.hero_win_rate_report import HeroWinRateReportService
from service.qq_command_discovery import QQCommandDiscoveryService

PRIVATE_HERO_REPORT_COMMAND = HERO_COMMAND.name
HERO_REPORT_REPLY_TIMEOUT_SECONDS = 20.0
HERO_REPORT_CACHE_SECONDS = 300.0
# 网络业务与存储隔离；进程共享准入，避免重建客户端绕过上限或累积线程。
_COMMAND_EXECUTOR = ThreadPoolExecutor(max_workers=command_workers, thread_name_prefix="qq-command")
_COMMAND_SLOTS = BoundedSemaphore(command_workers)


class MyClient(botpy.Client):
    def __init__(
        self,
        *args,
        router: CommandRouter,
        hero_win_rate_report: HeroWinRateReportService,
        command_discovery: QQCommandDiscoveryService | None = None,
        hero_report_reply_timeout: float = HERO_REPORT_REPLY_TIMEOUT_SECONDS,
        memory_store: ConversationMemory,
        **kwargs,
    ) -> None:
        configure_qq_sdk_security()
        # SDK DEBUG includes authorization headers and message bodies, including with --debug.
        kwargs["log_level"] = logging.INFO
        super().__init__(*args, **kwargs)
        self.router = router
        # 依赖由启动入口装配，不再修改路由或隐式创建另一个统计客户端。
        self.memory_store = memory_store
        self.hero_win_rate_report = hero_win_rate_report
        self.command_discovery = command_discovery or QQCommandDiscoveryService(
            self.api._http.request
        )
        self.hero_report_reply_timeout = hero_report_reply_timeout
        # 单个任务引用同时保活后台查询和保留缓存，无需另一份任务集合。
        self._hero_report_task: asyncio.Task[str] | None = None
        self._hero_report_completed_at = 0.0
        self._hero_report_refresh_task: asyncio.Task[None] | None = None
        self._hero_report_day: date | None = None
        self._command_discovery_configured = False
        # 弱引用避免访问过的会话永久累积，同时让同会话状态变更保持顺序。
        self._message_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def on_ready(self) -> None:
        _log.info(f"robot 「{self.robot.name}」 on_ready!")
        # 重连可能再次触发 on_ready；只保留一个预热与定期补齐任务。
        if self._hero_report_refresh_task is None or self._hero_report_refresh_task.done():
            self._hero_report_refresh_task = asyncio.create_task(self._refresh_hero_reports())
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
        lock = self._message_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._message_locks[conversation_id] = lock
        async with lock:
            await self._handle_message_locked(
                message, conversation_id, speaker_id, is_group=is_group
            )

    async def _handle_message_locked(
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

        record_reply = True
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
                # 提交前快速拒绝，不让慢 AI、参数追问或简报占满存储线程或排队。
                if not _COMMAND_SLOTS.acquire(blocking=False):
                    reply = "当前查询繁忙，请稍后再试。"
                else:
                    try:
                        future = _COMMAND_EXECUTOR.submit(
                            copy_context().run, self.router.dispatch, message.content, context
                        )
                    except BaseException:
                        _COMMAND_SLOTS.release()
                        raise
                    # 取消等待不能停止运行中的线程，必须等真实工作完成才回收容量。
                    future.add_done_callback(lambda _future: _COMMAND_SLOTS.release())
                    reply = await asyncio.wrap_future(future)
                    # 只有实际执行清空时才不记确认；繁忙提示仍正常落盘。
                    if normalized_content == CLEAR_COMMAND.name:
                        record_reply = False
        except Exception:
            _log.exception("处理对话失败")
            reply = "处理失败了，稍后再试。"

        try:
            # 发送前保存生成的回复，发送失败仍保留此次输入与回复尝试。
            if record_reply:
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

    async def _refresh_hero_reports(self) -> None:
        # 启动即预热，定期检查可在跨 UTC 日后补齐，并让失败查询冷却后重试。
        while True:
            try:
                await self._build_hero_report_with_deadline()
            except Exception:
                _log.exception("预热英雄胜率数据失败")
            await asyncio.sleep(HERO_REPORT_CACHE_SECONDS)

    async def _build_hero_report_with_deadline(self) -> str:
        """优先交付完整快照，后台刷新去重并限制失败重试频率。"""
        loop = asyncio.get_running_loop()
        today = datetime.now(timezone.utc).date()
        task = self._hero_report_task
        if task is not None and task.done() and not task.cancelled() and task.exception() is None:
            if self._hero_report_day == today and loop.time() - self._hero_report_completed_at < HERO_REPORT_CACHE_SECONDS:
                return task.result()

        # 读取磁盘快照不等待网络刷新锁，首次查询与进程重启都能立即使用旧榜单。
        cached = await asyncio.to_thread(self.hero_win_rate_report.build_cached)
        # await 期间任务可能已完成或被其他请求替换；随后判断与创建不再让出事件循环。
        task = self._hero_report_task
        if task is not None and task.done():
            cooling_down = loop.time() - self._hero_report_completed_at < HERO_REPORT_CACHE_SECONDS
            if task.cancelled() or task.exception() is not None:
                if cooling_down:
                    return cached or "英雄胜率更新失败，请稍后再次查询。"
            elif self._hero_report_day == today and cooling_down:
                return task.result()
            task = None
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(self.hero_win_rate_report.build))
            self._hero_report_task = task
            self._hero_report_day = today
            task.add_done_callback(self._finish_background_report)
        if cached is not None:
            return cached
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), timeout=self.hero_report_reply_timeout
            )
        except TimeoutError:
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

    router, hero_report, memory_store = build_application()
    intents = botpy.Intents(public_messages=True)
    client = MyClient(
        intents=intents,
        router=router,
        hero_win_rate_report=hero_report,
        memory_store=memory_store,
        # 容器日志交给 stdout/stderr 和 Docker 收集，避免 SDK 在应用目录写日志文件。
        ext_handlers=False,
    )
    client.run(appid=app_id, secret=app_secret)


if __name__ == "__main__":
    start()
