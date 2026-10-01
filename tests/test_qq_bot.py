import asyncio
import time
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


def test_hero_report_failure_allows_retry() -> None:
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
    return CommandRouter(BotServices(**defaults), memory_store=qq_bot.test_memory_store)


def test_empty_message_returns_help() -> None:
    assert "指令列表" in build_router().dispatch("   ")


def test_track_validates_and_saves_dota_id() -> None:
    saved: list[tuple[str, int]] = []
    router = build_router(set_dota_id=lambda nickname, dota_id: saved.append((nickname, dota_id)))

    assert "可以看看" in router.dispatch("追踪术 小明 123")
    assert saved == [("小明", 123)]
    assert "dotaId 必须是正整数" in router.dispatch("追踪术 小明 abc")


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


def test_command_errors_ask_for_parameters_without_general_chat() -> None:
    router = build_router()

    assert "请直接回复昵称" in router.dispatch("追踪术")
    assert "请直接回复昵称" in router.dispatch("撒情况")
    assert "请直接回复昵称" in router.dispatch("撒情况")
    assert router.dispatch("撒情况 陌生人") == "还没有追踪选手「陌生人」。"


def test_legacy_menu_placeholders_are_treated_as_missing_arguments() -> None:
    router = build_router()

    assert "请直接回复昵称" in router.dispatch("追踪术 昵称 dotaId")
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
