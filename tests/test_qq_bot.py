import asyncio
from types import SimpleNamespace

import botpy

from qq_bot import BotServices, CommandContext, CommandRouter, MyClient


def build_router(**overrides) -> CommandRouter:
    """用内存替身隔离网络和文件系统，验证命令路由本身。"""
    defaults = {
        "set_dota_id": lambda _nickname, _dota_id: None,
        "get_dota_id": lambda nickname: 123 if nickname == "小明" else None,
        "get_recent_matches": lambda dota_id: f"比赛:{dota_id}",
        "get_player_wl": lambda _dota_id, _days: (2, 1),
        "get_today_report": lambda: "今日简报",
        "chat": lambda message, _conversation_id: f"AI:{message}",
    }
    defaults.update(overrides)
    return CommandRouter(BotServices(**defaults))


def test_empty_message_returns_help() -> None:
    assert "指令列表" in build_router().dispatch("   ")


def test_track_validates_and_saves_dota_id() -> None:
    saved: list[tuple[str, int]] = []
    router = build_router(set_dota_id=lambda nickname, dota_id: saved.append((nickname, dota_id)))

    assert "可以看看" in router.dispatch("追踪术 小明 123")
    assert saved == [("小明", 123)]
    assert router.dispatch("追踪术 小明 abc") == "dotaId 必须是数字。"


def test_known_commands_are_dispatched() -> None:
    router = build_router()

    assert router.dispatch("撒情况 小明") == "比赛:123"
    assert router.dispatch("今儿 小明") == "胜:2, 败:1"
    assert router.dispatch("简报") == "今日简报"


def test_command_errors_do_not_fall_through_to_ai() -> None:
    router = build_router()

    assert router.dispatch("撒情况") == "用法: 撒情况 昵称"
    assert router.dispatch("今儿 陌生人") == "还没有追踪选手「陌生人」。"


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


def test_weekly_report_is_scheduled_at_20_00() -> None:
    async def create_client() -> MyClient:
        # botpy 需要运行中的事件循环；测试只检查配置，不启动网络连接。
        return MyClient(
            intents=botpy.Intents(public_messages=True),
            router=build_router(),
            report_group_openid="group-id",
            ext_handlers=False,
        )

    client = asyncio.run(create_client())

    job = client.scheduler.get_job("weekly-hero-win-rate-report")

    assert job is not None
    assert str(job.trigger) == "cron[hour='20', minute='0']"


def test_manual_report_uses_scheduled_target_and_sender() -> None:
    class FakeWeeklyReport:
        def build(self) -> str:
            return "测试榜单正文"

    class FakeApi:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def post_group_message(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(id="sent-message-id")

    class FakeMessage:
        content = "测试英雄胜率榜"
        group_openid = "command-source-group"

        def __init__(self) -> None:
            self.replies: list[dict[str, object]] = []

        async def reply(self, **kwargs):
            self.replies.append(kwargs)
            return SimpleNamespace(id="reply-message-id")

    async def run_manual_test() -> tuple[FakeApi, FakeMessage]:
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=build_router(),
            report_group_openid="scheduled-target-group",
            weekly_report=FakeWeeklyReport(),
            ext_handlers=False,
        )
        fake_api = FakeApi()
        client.api = fake_api
        message = FakeMessage()
        await client.on_group_at_message_create(message)
        return fake_api, message

    fake_api, message = asyncio.run(run_manual_test())

    assert fake_api.calls == [
        {
            "group_openid": "scheduled-target-group",
            "msg_type": 0,
            "content": "测试榜单正文",
        }
    ]
    assert message.replies[0]["content"] == "测试榜单已发送到定时任务目标群。"


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
            chat=lambda message, conversation_id: (
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
        def build(self) -> str:
            return "上周五位置英雄胜率榜"

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
            chat=lambda message, conversation_id: ai_calls.append(
                (message, conversation_id)
            )
        )
        client = MyClient(
            intents=botpy.Intents(public_messages=True),
            router=router,
            weekly_report=FakeWeeklyReport(),
            ext_handlers=False,
        )
        message = FakeMessage()
        await client.on_c2c_message_create(message)
        return message

    message = asyncio.run(run_private_report())

    assert ai_calls == []
    assert message.replies == [
        {"msg_type": 0, "content": "上周五位置英雄胜率榜"}
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
