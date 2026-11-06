import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from threading import Event
from types import SimpleNamespace

import botpy
import pytest

from lib.conversation_memory import ConversationMemory

import qq_bot
from qq_bot import BotServices, CommandContext, CommandRouter, MyClient as SDKClient


def test_hero_report_reuses_pending_task_and_completed_result() -> None:
    release = Event()
    calls = []

    def build() -> str:
        calls.append(True)
        assert release.wait(3)
        return "真实胜率榜"

    async def run() -> None:
        bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(build=build),
            hero_report_reply_timeout=0.02,
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        try:
            replies = await asyncio.gather(
                bot._build_hero_report_with_deadline(),
                bot._build_hero_report_with_deadline(),
            )
            assert len(calls) == 1
            assert replies == ["英雄胜率数据正在更新，请稍后再次查询。"] * 2
        finally:
            release.set()
            await bot._hero_report_task
        assert await bot._build_hero_report_with_deadline() == "真实胜率榜"
        assert len(calls) == 1
        # 完整结果只短暂复用，过期后重新生成，避免榜单长期固定。
        bot._hero_report_completed_at -= qq_bot.HERO_REPORT_CACHE_SECONDS
        assert await bot._build_hero_report_with_deadline() == "真实胜率榜"
        assert len(calls) == 2

    asyncio.run(run())


def test_hero_report_failure_cools_down_before_retry() -> None:
    calls = []

    def build() -> str:
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("refresh failed")
        return "恢复后的榜单"

    async def run() -> None:
        bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(build=build),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        with pytest.raises(RuntimeError, match="refresh failed"):
            await bot._build_hero_report_with_deadline()
        assert await bot._build_hero_report_with_deadline() == "英雄胜率更新失败，请稍后再次查询。"
        assert len(calls) == 1
        bot._hero_report_completed_at -= qq_bot.HERO_REPORT_CACHE_SECONDS
        assert await bot._build_hero_report_with_deadline() == "恢复后的榜单"
        assert len(calls) == 2

    asyncio.run(run())


@pytest.fixture(autouse=True)
def memory_store(tmp_path, monkeypatch) -> ConversationMemory:
    # SDK 回调真实执行压缩落盘，但测试不碰生产数据目录。
    store = ConversationMemory(tmp_path / "conversations")
    monkeypatch.setattr(qq_bot, "test_memory_store", store, raising=False)
    return store


def MyClient(*args, **kwargs):
    # 测试显式装配替身，生产 SDK 不再隐式创建业务依赖。
    kwargs.setdefault("memory_store", qq_bot.test_memory_store)
    kwargs.setdefault("hero_win_rate_report", SimpleNamespace(build=lambda: "英雄榜"))
    report = kwargs["hero_win_rate_report"]
    if not hasattr(report, "build_cached"):
        report.build_cached = lambda: None
    return SDKClient(*args, **kwargs)


def build_router(**overrides) -> CommandRouter:
    """用内存替身隔离网络和文件系统，验证命令路由本身。"""
    defaults = {
        "set_dota_id": lambda _nickname, _dota_id: None,
        "get_dota_id": lambda nickname: 123 if nickname == "小明" else None,
        "get_recent_matches": lambda dota_id: f"比赛:{dota_id}",
        "get_today_report": lambda: "今日简报",
        "chat": lambda message, _conversation_id, _before_id=None: f"AI:{message}",
        "list_player_nicknames": lambda: [],
    }
    defaults.update(overrides)
    return CommandRouter(
        BotServices(**defaults),
        memory_store=qq_bot.test_memory_store,
        # 测试统一把 user-a 设为已配置操作者，写操作才允许执行。
        operator_ids=frozenset({"user-a"}),
    )


OPERATOR_CONTEXT = CommandContext(conversation_id="c2c:user-a", speaker_id="user-a")


@pytest.fixture
def command_pool(monkeypatch):
    # 两个业务槽和两个默认线程即可稳定复现旧实现的共享线程池饥饿。
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(qq_bot, "_COMMAND_EXECUTOR", pool, raising=False)
        monkeypatch.setattr(qq_bot, "_COMMAND_SLOTS", BoundedSemaphore(2), raising=False)
        yield pool


@pytest.fixture
def ai_pool(monkeypatch):
    """与 command_pool 对称的 AI 线程池 mock，2 worker + 2 槽。"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(qq_bot, "_AI_EXECUTOR", pool, raising=False)
        monkeypatch.setattr(qq_bot, "_AI_SLOTS", BoundedSemaphore(2), raising=False)
        yield pool


def _run_isolation_test(pool_to_fill: str, content_to_fill: str) -> None:
    """bot 用 slow router 占满指定池，other 用正常 router 验证另一池不受影响。"""
    release = Event()
    started = Event()

    def slow(*_args):
        # 所有路由入口都走 slow，确保一定能占满
        started.set()
        assert release.wait(5)
        return "完成"

    async def run() -> None:
        # bot 用 slow router 占满池
        slow_bot = MyClient(
            router=build_router(
                chat=slow, ask_command_parameter=slow,
                get_today_report=slow, get_recent_matches=slow,
            ),
            hero_win_rate_report=SimpleNamespace(
                build=lambda: "新榜单", build_cached=lambda: "缓存榜单",
            ),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        # other 用正常 router，不受 slow 影响
        normal_bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(
                build=lambda: "新榜单", build_cached=lambda: "缓存榜单",
            ),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )

        class Message:
            def __init__(self, text):
                self.content = text
                self.replies: list[str] = []

            async def reply(self, **kwargs):
                self.replies.append(kwargs["content"])
                return SimpleNamespace(id="reply-id")

        # 先启动两条占池消息
        tasks = []
        for i in range(2):
            tasks.append(asyncio.create_task(
                slow_bot._handle_message(
                    Message(content_to_fill), f"c2c:{i}", str(i)
                )
            ))
        # 等 slow 线程池里的至少一条进入 started
        await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 1)

        try:
            if pool_to_fill == "ai":
                # AI 池满 → 命令池应正常
                clear_msg = Message("/clear")
                await asyncio.wait_for(
                    normal_bot._handle_message(clear_msg, "c2c:clear", "clear"), 1
                )
                assert clear_msg.replies == ["已清空当前会话的对话记录，已绑定选手资料保留。"]
            else:
                # 命令池满 → AI 池应正常
                chat_msg = Message("聊天")
                await asyncio.wait_for(
                    normal_bot._handle_message(chat_msg, "c2c:chat", "chat"), 1
                )
                assert chat_msg.replies == ["AI:聊天"]
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(run())


def test_ai_pool_full_command_pool_still_responds(ai_pool) -> None:
    """AI 池被占满时，确定性命令走命令池，不受影响。"""
    _run_isolation_test(pool_to_fill="ai", content_to_fill="聊天")


def test_command_pool_full_ai_pool_still_responds(command_pool) -> None:
    """命令池被占满时，普通聊天走 AI 池，不受影响。"""
    _run_isolation_test(pool_to_fill="command", content_to_fill="撒情况 小明")


def test_pool_busy_when_same_pool_full(ai_pool, command_pool) -> None:
    """占满一个池后，向同一池发第三条消息应立即返回繁忙。"""
    release = Event()
    started = Event()

    def slow(*_args):
        started.set()
        assert release.wait(5)
        return "完成"

    async def run() -> None:
        slow_bot = MyClient(
            router=build_router(
                chat=slow, ask_command_parameter=slow,
                get_today_report=slow, get_recent_matches=slow,
            ),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        normal_bot = MyClient(
            router=build_router(),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )

        class Message:
            def __init__(self, text):
                self.content = text
                self.replies: list[str] = []

            async def reply(self, **kwargs):
                self.replies.append(kwargs["content"])
                return SimpleNamespace(id="reply-id")

        # 占满 command pool
        cmd_tasks = []
        for i in range(2):
            cmd_tasks.append(asyncio.create_task(
                slow_bot._handle_message(
                    Message("撒情况 小明"), f"c2c:cmd-{i}", f"cmd-{i}"
                )
            ))
        await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 1)

        try:
            # 第三条命令：command pool 已满 → 繁忙
            busy_cmd = Message("撒情况 小明")
            await asyncio.wait_for(
                normal_bot._handle_message(busy_cmd, "c2c:busy-cmd", "busy-cmd"), 1
            )
            assert busy_cmd.replies == ["当前查询繁忙，请稍后再试。"]

            # 但 AI 消息走另一池，应正常
            chat_msg = Message("聊天")
            await asyncio.wait_for(
                normal_bot._handle_message(chat_msg, "c2c:chat", "chat"), 1
            )
            assert chat_msg.replies == ["AI:聊天"]
        finally:
            release.set()
            await asyncio.gather(*cmd_tasks)

        # 释放后 command pool 恢复
        cmd_msg = Message("撒情况 小明")
        await asyncio.wait_for(
            normal_bot._handle_message(cmd_msg, "c2c:recovered", "recovered"), 1
        )
        assert cmd_msg.replies == ["比赛:123"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "pool_fixture,content,pool_module_attr",
    [
        ("ai_pool", "聊天", "_AI_EXECUTOR"),
        ("command_pool", "撒情况 小明", "_COMMAND_EXECUTOR"),
    ],
)
def test_cancelled_message_keeps_slot_until_worker_finishes(
    request, pool_fixture, content, pool_module_attr
) -> None:
    """无论哪个池，被 cancel 的任务都应等 worker 真实完成后才释放 slot。"""
    request.getfixturevalue(pool_fixture)
    pool = getattr(qq_bot, pool_module_attr)
    release = Event()

    async def run() -> None:
        loop = asyncio.get_running_loop()
        started = [asyncio.Event(), asyncio.Event()]
        calls: list[bool] = []

        def slow(*_args):
            index = len(calls)
            calls.append(True)
            loop.call_soon_threadsafe(started[index].set)
            assert release.wait(5)
            return "完成"

        def build_slow_router():
            return build_router(
                chat=slow, get_recent_matches=lambda *_args: slow()
            )

        bot = MyClient(
            router=build_slow_router(),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )

        class Message:
            def __init__(self, text):
                self.content = text
                self.replies: list[str] = []

            async def reply(self, **kwargs):
                self.replies.append(kwargs["content"])
                return SimpleNamespace(id="reply-id")

        replies: list[str] = []
        tasks = [
            asyncio.create_task(
                bot._handle_message(Message(content), f"c2c:{i}", str(i))
            )
            for i in range(2)
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started)), 1
            )
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            # 第三条消息：同一池已被两条占满，cancel 只取消了 future 但 worker 还没退出
            extra = Message(content)
            await asyncio.wait_for(
                bot._handle_message(extra, "c2c:extra", "extra"), 1
            )
            assert extra.replies == ["当前查询繁忙，请稍后再试。"]
            assert len(calls) == 2
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            # 等待同池中 worker 真正执行完槽位释放回调
            await asyncio.wrap_future(pool.submit(lambda: None))
        # 恢复正常
        bot.router = build_router()
        recovered = Message(content)
        await asyncio.wait_for(
            bot._handle_message(recovered, "c2c:recovered", "recovered"), 1
        )
        assert recovered.replies and recovered.replies != ["当前查询繁忙，请稍后再试。"]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["dispatch", "submit"])
@pytest.mark.parametrize(
    "pool_fixture,content",
    [
        ("ai_pool", "聊天"),
        ("command_pool", "撒情况 小明"),
    ],
)
def test_pool_failure_returns_capacity(request, failure, pool_fixture, content) -> None:
    """executor.submit 或 router.dispatch 抛异常时，slot 必须被释放。"""
    request.getfixturevalue(pool_fixture)
    replies: list[str] = []

    def fail(*_args):
        raise RuntimeError("handler failed")

    class Message:
        def __init__(self, text):
            self.content = text
            self.replies: list[str] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs["content"])
            replies.append(kwargs["content"])
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        bot = MyClient(
            router=SimpleNamespace(dispatch=fail),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        # 每条消息都走同一个池；3 条连续失败后池应该恢复到可用状态
        for i in range(3):
            await bot._handle_message(
                Message(content), f"c2c:failed-{i}", f"failed-{i}"
            )
        assert replies == ["处理失败了，稍后再试。"] * 3
        # 恢复正常 router 后应该仍能执行
        bot.router = build_router()
        recovered = Message(content)
        await bot._handle_message(recovered, "c2c:recovered", "recovered")
        assert recovered.replies != ["处理失败了，稍后再试。"]

    asyncio.run(run())


def test_command_dispatch_preserves_context(command_pool) -> None:
    from contextvars import ContextVar

    request_context = ContextVar("request_context", default="missing")
    replies = []

    class Message:
        content = "聊天"

        async def reply(self, **kwargs):
            replies.append(kwargs["content"])
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        request_context.set("current-request")
        bot = MyClient(
            router=build_router(chat=lambda *_args: request_context.get()),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        await bot._handle_message(Message(), "c2c:alice", "alice")

    asyncio.run(run())
    assert replies == ["current-request"]


def test_same_conversation_messages_are_processed_in_order(memory_store) -> None:
    started = Event()
    release = Event()
    calls: list[str] = []

    def dispatch(content, _context):
        calls.append(content)
        if content == "第一条":
            started.set()
            assert release.wait(3)
        return f"回复:{content}"

    class Message:
        group_openid = None

        def __init__(self, content):
            self.content = content

        async def reply(self, **_kwargs):
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        bot = MyClient(
            router=SimpleNamespace(dispatch=dispatch),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        first = asyncio.create_task(
            bot._handle_message(Message("第一条"), "c2c:alice", "alice")
        )
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(
            bot._handle_message(Message("第二条"), "c2c:alice", "alice")
        )
        await asyncio.sleep(0.02)
        assert calls == ["第一条"]
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(run())

    assert [record["content"] for record in memory_store.read("c2c:alice")] == [
        "第一条", "回复:第一条", "第二条", "回复:第二条",
    ]


def test_empty_message_returns_help() -> None:
    assert "指令列表" in build_router().dispatch("   ")


def test_track_validates_and_saves_dota_id() -> None:
    saved: list[tuple[str, int]] = []
    router = build_router(set_dota_id=lambda nickname, dota_id: saved.append((nickname, dota_id)))

    assert "可以看看" in router.dispatch("追踪术 小明 123", OPERATOR_CONTEXT)
    assert saved == [("小明", 123)]
    assert "dotaId 必须是正整数" in router.dispatch(
        "追踪术 小明 abc", OPERATOR_CONTEXT
    )


def test_group_member_cannot_override_global_binding() -> None:
    saved: list[tuple[str, int]] = []
    router = build_router(set_dota_id=lambda nickname, dota_id: saved.append((nickname, dota_id)))
    member = CommandContext(conversation_id="group:room", speaker_id="bob")

    assert "管理员" in router.dispatch("追踪术 小明 999", member)
    assert saved == []


def test_known_commands_are_dispatched() -> None:
    router = build_router()

    assert router.dispatch("撒情况 小明") == "比赛:123"
    assert router.dispatch("撒情况 小明") == "比赛:123"
    assert router.dispatch("简报") == "今日简报"


def test_command_panel_slash_prefix_is_ignored() -> None:
    router = build_router()

    assert router.dispatch("/撒情况 小明") == "比赛:123"
    assert router.dispatch("／撒情况 小明") == "比赛:123"
    assert router.dispatch(" /简报 ") == "今日简报"
    assert router.dispatch(
        "/群OpenID", CommandContext(group_openid="group-openid")
    ) == "当前群 OpenID：group-openid"


@pytest.mark.parametrize(
    "content,expected",
    [
        ("聊天", True),
        ("你好小明", True),
        ("", False),
        ("  ", False),
        ("/clear", False),
        ("clear", False),
        ("简报", False),
        ("撒情况 小明", False),
        ("追踪术 小明 123", False),
        ("群OpenID", False),
        ("查看当前群OpenID", False),
        ("高胜率英雄", False),
        ("/撒情况 小明", False),   # 带斜杠也应识别为命令
        ("／简报", False),          # 全角斜杠
        ("clear extra  garbage", False),  # 已知命令名开头就走命令池
    ],
)
def test_is_ai_chat_content_route_prediction(content: str, expected: bool) -> None:
    """路由预判：已知命令 → False（走命令池），其他 → True（走 AI 池）。"""
    assert qq_bot._is_ai_chat_content(content) is expected


def test_command_errors_ask_for_parameters_without_general_chat() -> None:
    router = build_router()

    assert "请直接回复昵称" in router.dispatch("追踪术", OPERATOR_CONTEXT)
    assert "请直接回复昵称" in router.dispatch("撒情况")
    assert "请直接回复昵称" in router.dispatch("撒情况")
    assert router.dispatch("撒情况 陌生人") == "还没有追踪选手「陌生人」。"


def test_legacy_menu_placeholders_are_treated_as_missing_arguments() -> None:
    router = build_router()

    assert "请直接回复昵称" in router.dispatch("追踪术 昵称 dotaId", OPERATOR_CONTEXT)
    assert "请直接回复昵称" in router.dispatch("撒情况 昵称")
    assert "请直接回复昵称" in router.dispatch("撒情况 昵称")


def test_unknown_message_uses_configured_fallback() -> None:
    assert build_router().dispatch("你好") == "AI:你好"
    assert CommandRouter(build_router().services, ai_enabled=False).dispatch("你好") == "听不懂。"


def test_any_group_member_can_read_current_group_openid() -> None:
    reply = build_router().dispatch(
        "查看当前群OpenID", CommandContext(group_openid="group-openid")
    )

    assert reply == "当前群 OpenID：group-openid"


def test_short_group_openid_alias_supports_command_panel_limit() -> None:
    reply = build_router().dispatch(
        "群OpenID", CommandContext(group_openid="group-openid")
    )

    assert reply == "当前群 OpenID：group-openid"


def test_group_openid_command_requires_group_context() -> None:
    assert build_router().dispatch("查看当前群OpenID") == "当前消息不包含群 OpenID。"


def test_group_hero_report_replies_to_current_request() -> None:
    class FakeWeeklyReport:
        def build(self, ) -> str:
            return "当前英雄胜率榜"

    class FakeMessage:
        group_openid = "current-group"

        def __init__(self, content: str) -> None:
            self.content = content
            self.replies: list[dict[str, object]] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return SimpleNamespace(id="reply-message-id")

    async def run_requests() -> list[FakeMessage]:
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=build_router(),
            hero_win_rate_report=FakeWeeklyReport(),
            ext_handlers=False,
        )
        messages = [
            FakeMessage("/高胜率英雄"),
            # 兼容 QQ 客户端短期缓存的旧指令面板按钮。
            FakeMessage("/测试胜率榜"),
        ]
        for message in messages:
            await client.on_group_at_message_create(message)
        return messages

    messages = asyncio.run(run_requests())

    assert [message.replies for message in messages] == [
        [{"msg_type": 0, "content": "当前英雄胜率榜"}],
        [{"msg_type": 0, "content": "当前英雄胜率榜"}],
    ]


def test_slow_group_hero_report_replies_before_background_update_finishes(memory_store) -> None:
    class SlowWeeklyReport:
        def build(self, ) -> str:
            time.sleep(0.05)
            return "后台生成的英雄胜率榜"

    class FakeMessage:
        content = "/高胜率英雄"
        group_openid = "current-group"

        def __init__(self) -> None:
            self.replies: list[dict[str, object]] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return SimpleNamespace(id="reply-message-id")

    async def run_request() -> FakeMessage:
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=build_router(),
            hero_win_rate_report=SlowWeeklyReport(),
            hero_report_reply_timeout=0.001,
            ext_handlers=False,
        )
        message = FakeMessage()
        await client.on_group_at_message_create(message)
        # 等待后台线程结束，验证超时只影响当次回复，不会取消缓存更新。
        await asyncio.sleep(0.1)
        assert client._hero_report_task.done()
        return message

    message = asyncio.run(run_request())

    assert message.replies == [
        {"msg_type": 0, "content": "英雄胜率数据正在更新，请稍后再次查询。"}
    ]
    assert memory_store.read("group:current-group")[-1] == {
        "role": "assistant", "content": "英雄胜率数据正在更新，请稍后再次查询。"
    }


def test_private_message_uses_deepseek_and_c2c_reply() -> None:
    calls: list[tuple[str, str]] = []

    class FakeMessage:
        content = "私聊你好"
        id = "incoming-message-id"
        author = SimpleNamespace(user_openid="user-openid")

        def __init__(self) -> None:
            self.replies: list[dict[str, object]] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return SimpleNamespace(id="reply-message-id")

    async def run_private_message() -> FakeMessage:
        router = build_router(
            chat=lambda message, conversation_id, _before_id=None: (
                calls.append((message, conversation_id)) or f"AI:{message}"
            )
        )
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=router,
            ext_handlers=False,
        )
        message = FakeMessage()
        await client.on_c2c_message_create(message)
        return message

    message = asyncio.run(run_private_message())

    assert calls == [("私聊你好", "c2c:user-openid")]
    assert message.replies == [{"msg_type": 0, "content": "AI:私聊你好"}]


def test_private_hero_report_keyword_replies_to_requester() -> None:
    ai_calls: list[tuple[str, str]] = []

    class FakeWeeklyReport:
        def build(self, ) -> str:
            return "当前全分段英雄胜率 Top 10"

    class FakeMessage:
        content = "  高胜率英雄  "
        id = "incoming-message-id"
        author = SimpleNamespace(user_openid="user-openid")

        def __init__(self) -> None:
            self.replies: list[dict[str, object]] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return SimpleNamespace(id="reply-message-id")

    async def run_private_report() -> FakeMessage:
        router = build_router(
            chat=lambda message, conversation_id, _before_id=None: ai_calls.append(
                (message, conversation_id)
            )
        )
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=router,
            hero_win_rate_report=FakeWeeklyReport(),
            ext_handlers=False,
        )
        message = FakeMessage()
        await client.on_c2c_message_create(message)
        return message

    message = asyncio.run(run_private_report())

    assert ai_calls == []
    assert message.replies == [
        {"msg_type": 0, "content": "当前全分段英雄胜率 Top 10"}
    ]


def test_private_menu_command_uses_command_router() -> None:
    class FakeMessage:
        content = "简报"
        id = "incoming-message-id"
        author = SimpleNamespace(user_openid="user-openid")

        def __init__(self) -> None:
            self.replies: list[dict[str, object]] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return SimpleNamespace(id="reply-message-id")

    async def run_private_command() -> FakeMessage:
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=build_router(),
            ext_handlers=False,
        )
        message = FakeMessage()
        await client.on_c2c_message_create(message)
        return message

    message = asyncio.run(run_private_command())

    assert message.replies == [{"msg_type": 0, "content": "今日简报"}]


def test_start_disables_sdk_file_logging(monkeypatch) -> None:
    created: dict[str, object] = {}
    run_arguments: dict[str, str] = {}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            created.update(kwargs)

        def run(self, **kwargs) -> None:
            run_arguments.update(kwargs)

    monkeypatch.setenv("QQBOT_APP_ID", "test-app-id")
    monkeypatch.setenv("QQBOT_APP_SECRET", "test-secret")
    monkeypatch.setattr(qq_bot, "MyClient", FakeClient)
    monkeypatch.setattr(qq_bot, "CommandRouter", lambda: object())

    qq_bot.start()

    assert created["ext_handlers"] is False
    assert run_arguments == {"appid": "test-app-id", "secret": "test-secret"}


@pytest.mark.parametrize("is_group", [False, True])
@pytest.mark.parametrize(
    "content,expected",
    [
        ("简报", "今日简报"),
        ("你好", "AI:你好"),
        ("/高胜率英雄", "英雄胜率榜"),
        ("错误", "处理失败了，稍后再试。"),
    ],
)
def test_every_received_message_and_generated_reply_is_persisted(
    memory_store, is_group: bool, content: str, expected: str
) -> None:
    def chat(message: str, _conversation_id: str, _before_id=None) -> str:
        if message == "错误":
            raise RuntimeError("analysis unavailable")
        return f"AI:{message}"

    class FakeMessage:
        group_openid = "group-a"
        author = SimpleNamespace(user_openid="user-a", member_openid="member-a")

        async def reply(self, **kwargs):
            assert kwargs["content"] == expected
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        client = MyClient(
            router=build_router(chat=chat),
            hero_win_rate_report=SimpleNamespace(build=lambda : "英雄胜率榜"),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        message = FakeMessage()
        message.content = content
        if is_group:
            await client.on_group_at_message_create(message)
        else:
            await client.on_c2c_message_create(message)

    asyncio.run(run())
    conversation_id = "group:group-a" if is_group else "c2c:user-a"
    assert memory_store.read(conversation_id) == [
        {"role": "user", "content": content},
        {"role": "assistant", "content": expected},
    ]


def test_persisted_command_reply_is_used_by_ai_after_restart(
    memory_store, monkeypatch
) -> None:
    from unittest.mock import Mock

    import lib.deepseek_api as deepseek_api

    class FakeMessage:
        author = SimpleNamespace(user_openid="user-a")

        def __init__(self, content: str) -> None:
            self.content = content

        async def reply(self, **kwargs):
            return SimpleNamespace(id="reply-id")

    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="记得今日简报"))]
    )
    monkeypatch.setattr(deepseek_api, "get_client", lambda: client)
    restarted = ConversationMemory(memory_store.root)
    monkeypatch.setattr(deepseek_api, "get_memory_store", lambda: restarted)

    async def run() -> None:
        for content in ("简报", "继续"):
            # 每次创建新客户端，历史必须依靠磁盘恢复。
            bot = MyClient(
                router=build_router(chat=deepseek_api.deepseek_general),
                memory_store=ConversationMemory(memory_store.root),
                intents=botpy.Intents(public_messages=True),
                ext_handlers=False,
            )
            await bot.on_c2c_message_create(FakeMessage(content))

    asyncio.run(run())
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[1:] == [
        {"role": "user", "content": "简报"},
        {"role": "assistant", "content": "今日简报"},
        {"role": "user", "content": "继续"},
    ]
    assert restarted.read("c2c:user-a")[-1]["content"] == "记得今日简报"


def test_storage_failure_does_not_process_unrecorded_input(monkeypatch) -> None:
    from unittest.mock import Mock

    store = Mock()
    store.append.side_effect = OSError("disk unavailable")
    router = Mock()
    replies = []

    class FakeMessage:
        content = "你好"
        author = SimpleNamespace(user_openid="user-a")

        async def reply(self, **kwargs):
            replies.append(kwargs["content"])

    async def run() -> None:
        client = MyClient(
            router=router,
            memory_store=store,
            hero_win_rate_report=SimpleNamespace(build=lambda : "榜单"),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        await client.on_c2c_message_create(FakeMessage())

    asyncio.run(run())
    router.dispatch.assert_not_called()
    assert replies == ["对话记录存储失败，请稍后再试。"]


@pytest.mark.parametrize("is_group", [True, False])
def test_missing_identity_never_uses_shared_memory(memory_store, is_group) -> None:
    replies = []

    class FakeMessage:
        content = "你好"
        group_openid = None
        author = SimpleNamespace(user_openid=None)

        async def reply(self, **kwargs):
            replies.append(kwargs["content"])

    async def run() -> None:
        bot = MyClient(
            router=build_router(),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        if is_group:
            await bot.on_group_at_message_create(FakeMessage())
        else:
            await bot.on_c2c_message_create(FakeMessage())

    asyncio.run(run())
    assert not list(memory_store.root.iterdir())
    assert replies == ["对话记录存储失败，请稍后再试。"]


def test_sdk_private_parameter_answers_execute_and_record_both_roles(memory_store) -> None:
    saved = []
    replies = []

    class FakeMessage:
        author = SimpleNamespace(user_openid="user-a")

        def __init__(self, content: str) -> None:
            self.content = content

        async def reply(self, **kwargs):
            replies.append(kwargs["content"])
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        bot = MyClient(
            router=build_router(
                set_dota_id=lambda nickname, dota_id: saved.append((nickname, dota_id)),
                ask_command_parameter=lambda _command, field, _reason: f"请发{field}",
            ),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        for content in ("追踪术", "小明", "123"):
            await bot.on_c2c_message_create(FakeMessage(content))

    asyncio.run(run())
    assert replies[:2] == ["请发昵称", "请发dotaId"]
    assert saved == [("小明", 123)]
    history = memory_store.read("c2c:user-a")
    assert [item["role"] for item in history] == ["user", "assistant"] * 3
    assert [item["content"] for item in history[::2]] == ["追踪术", "小明", "123"]


def test_sdk_group_parameter_state_is_per_member_and_hero_command_cancels(memory_store) -> None:
    replies = []

    class FakeMessage:
        group_openid = "group-a"

        def __init__(self, content: str, member: str) -> None:
            self.content = content
            self.author = SimpleNamespace(member_openid=member)

        async def reply(self, **kwargs):
            replies.append(kwargs["content"])
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(build=lambda : "英雄榜"),
            intents=botpy.Intents(public_messages=True),
            ext_handlers=False,
        )
        for content, member in (
            ("撒情况", "alice"),
            ("小明", "bob"),
            ("小明", "alice"),
            ("撒情况", "alice"),
            ("高胜率英雄", "alice"),
            ("小明", "alice"),
        ):
            await bot.on_group_at_message_create(FakeMessage(content, member))

    asyncio.run(run())
    assert replies[1:3] == ["AI:小明", "比赛:123"]
    assert replies[-2:] == ["英雄榜", "AI:小明"]
    assert memory_store.get_pending("group:group-a", "alice") is None


@pytest.mark.parametrize("is_group", [False, True])
def test_clear_erases_current_conversation_without_recording_confirmation(memory_store, tmp_path, is_group) -> None:
    from lib.player_repository import PlayerRepository

    players = PlayerRepository(tmp_path / "players.json")
    players.set("小明", 123)
    conversation = "group:room" if is_group else "c2c:alice"
    memory_store.append(conversation, "user", "旧对话")
    memory_store.set_pending(conversation, "alice", ("追踪术", []))
    memory_store.append("c2c:bob", "user", "其他用户记录")
    replies = []

    class Message:
        content = "/clear"
        group_openid = "room"
        author = SimpleNamespace(user_openid="alice", member_openid="alice")

        async def reply(self, **kwargs):
            replies.append(kwargs["content"])
            return SimpleNamespace(id="reply-id")

    async def run() -> None:
        bot = MyClient(
            router=build_router(get_dota_id=players.get),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        if is_group:
            await bot.on_group_at_message_create(Message())
        else:
            await bot.on_c2c_message_create(Message())

    asyncio.run(run())
    assert "已清空" in replies[0]
    assert memory_store.read(conversation) == []
    assert memory_store.get_pending(conversation, "alice") is None
    assert memory_store.read("c2c:bob")[0]["content"] == "其他用户记录"
    assert PlayerRepository(players.file_path).get("小明") == 123


def test_clear_invalid_arguments_preserve_history_and_pending(memory_store) -> None:
    context = CommandContext(conversation_id="c2c:alice", speaker_id="alice")
    memory_store.append("c2c:alice", "user", "保留记录")
    memory_store.set_pending("c2c:alice", "alice", ("追踪术", []))
    router = build_router()
    assert router.dispatch("/clear extra", context) == "用法: /clear"
    assert memory_store.read("c2c:alice")[0]["content"] == "保留记录"
    assert memory_store.get_pending("c2c:alice", "alice") == ("追踪术", [])
    assert router.dispatch("/clear") == "当前消息不包含有效会话标识。"


def test_cached_report_returns_while_refresh_is_pending() -> None:
    release = Event()

    def build() -> str:
        assert release.wait(3)
        return "新榜单"

    async def run() -> None:
        bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(
                build=build, build_cached=lambda: "旧榜单及统计日期",
            ),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        try:
            assert await asyncio.wait_for(bot._build_hero_report_with_deadline(), 0.5) == "旧榜单及统计日期"
            assert not bot._hero_report_task.done()
        finally:
            release.set()
            await bot._hero_report_task
        assert await bot._build_hero_report_with_deadline() == "新榜单"

    asyncio.run(run())


def test_background_report_loop_prewarms_and_repeats(monkeypatch) -> None:
    async def run() -> None:
        bot = MyClient(router=build_router(), intents=botpy.Intents(public_messages=True), ext_handlers=False)
        calls = []

        async def refresh():
            calls.append(True)
            if len(calls) == 2:
                raise asyncio.CancelledError

        async def pause(seconds):
            assert seconds == qq_bot.HERO_REPORT_CACHE_SECONDS

        monkeypatch.setattr(bot, "_build_hero_report_with_deadline", refresh)
        monkeypatch.setattr(qq_bot.asyncio, "sleep", pause)
        with pytest.raises(asyncio.CancelledError):
            await bot._refresh_hero_reports()
        assert len(calls) == 2

    asyncio.run(run())


def test_on_ready_starts_only_one_refresh_loop(monkeypatch) -> None:
    async def run() -> None:
        configured = []

        async def configure():
            configured.append(True)

        bot = MyClient(
            router=build_router(), intents=botpy.Intents(public_messages=True),
            ext_handlers=False, command_discovery=SimpleNamespace(configure=configure),
        )
        monkeypatch.setattr(type(bot), "robot", property(lambda self: SimpleNamespace(name="test")))
        await bot.on_ready()
        task = bot._hero_report_refresh_task
        await bot.on_ready()
        assert bot._hero_report_refresh_task is task
        assert configured == [True]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_new_utc_day_refreshes_completed_report() -> None:
    async def run() -> None:
        calls = []
        bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(build=lambda: calls.append(True) or "榜单"),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        assert await bot._build_hero_report_with_deadline() == "榜单"
        bot._hero_report_day = bot._hero_report_day.replace(year=2020)
        assert await bot._build_hero_report_with_deadline() == "榜单"
        assert len(calls) == 2

    asyncio.run(run())


def test_failed_refresh_keeps_cached_report_during_cooldown() -> None:
    async def run() -> None:
        calls = []

        def build():
            calls.append(True)
            raise RuntimeError("offline")

        bot = MyClient(
            router=build_router(),
            hero_win_rate_report=SimpleNamespace(build=build, build_cached=lambda: "旧榜单"),
            intents=botpy.Intents(public_messages=True), ext_handlers=False,
        )
        assert await bot._build_hero_report_with_deadline() == "旧榜单"
        with pytest.raises(RuntimeError):
            await bot._hero_report_task
        assert await bot._build_hero_report_with_deadline() == "旧榜单"
        assert len(calls) == 1

    asyncio.run(run())


def test_client_disables_sensitive_sdk_debug_logs(monkeypatch, caplog) -> None:
    import logging
    import sys

    import ssl
    import botpy.gateway
    import botpy.http

    monkeypatch.setattr(botpy.http, "SSLContext", ssl.SSLContext)
    monkeypatch.setattr(botpy.gateway, "SSLContext", ssl.SSLContext)
    monkeypatch.setattr(sys, "argv", ["main.py", "--debug"])
    caplog.set_level(logging.DEBUG, logger="botpy")

    async def run():
        MyClient(router=build_router(), intents=botpy.Intents(public_messages=True),
                 ext_handlers=False, log_level=logging.DEBUG)
        logger = logging.getLogger("botpy")
        logger.debug("Authorization: synthetic-private-token; private-message")
        logger.info("safe status")

    asyncio.run(run())
    assert "synthetic-private-token" not in caplog.text
    assert "private-message" not in caplog.text
    assert "safe status" in caplog.text
    for factory in (botpy.http.SSLContext, botpy.gateway.SSLContext):
        context = factory()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
