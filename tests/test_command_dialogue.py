from unittest.mock import Mock

import pytest

from lib.conversation_memory import ConversationMemory
from service.command_router import BotServices, CommandContext, CommandRouter


def make_router(store, *, ask=None, saved=None, ai_enabled=True, bindings=None) -> CommandRouter:
    services = BotServices(
        set_dota_id=lambda nickname, dota_id: saved.append((nickname, dota_id)),
        get_dota_id={"小明": 123, "小红": 456}.get,
        get_recent_matches=lambda dota_id: f"比赛:{dota_id}",
        get_player_wl=lambda _dota_id, _days: (2, 1),
        get_today_report=lambda: "今日简报",
        chat=lambda message, _conversation_id, _before_id=None: f"AI:{message}",
        resolve_hero_name=lambda _hero_id: None,
        list_player_nicknames=lambda: ["小明", "小红"],
        ask_command_parameter=ask,
        list_player_bindings=bindings,
    )
    return CommandRouter(services, memory_store=store, ai_enabled=ai_enabled)


@pytest.fixture
def store(tmp_path) -> ConversationMemory:
    return ConversationMemory(tmp_path)


PRIVATE_CONTEXT = CommandContext(conversation_id="c2c:alice", speaker_id="alice")


def test_track_asks_each_missing_parameter_and_survives_restart(store) -> None:
    saved = []
    ask = Mock(side_effect=lambda command, field, reason: f"问：{field}")
    router = make_router(store, ask=ask, saved=saved)
    assert router.dispatch("/追踪术", PRIVATE_CONTEXT) == "问：昵称"
    assert router.dispatch("小明", PRIVATE_CONTEXT) == "问：dotaId"
    assert router.dispatch("   ", PRIVATE_CONTEXT) == "问：dotaId"
    restarted = make_router(ConversationMemory(store.root), ask=ask, saved=saved)
    assert "可以看看" in restarted.dispatch("123", PRIVATE_CONTEXT)
    assert saved == [("小明", 123)]
    assert store.get_pending("c2c:alice", "alice") is None
    assert restarted.dispatch("你好", PRIVATE_CONTEXT) == "AI:你好"


@pytest.mark.parametrize("command,expected", [("今儿", "胜:2, 败:1"), ("撒情况", "比赛:123")])
def test_player_commands_execute_after_nickname_answer(store, command, expected) -> None:
    router = make_router(store)
    assert "请直接回复昵称" in router.dispatch(command, PRIVATE_CONTEXT)
    assert router.dispatch("小明", PRIVATE_CONTEXT) == expected


@pytest.mark.parametrize("wrong_id", ["abc", "0", "-1", "dotaId"])
def test_invalid_id_keeps_correct_nickname(store, wrong_id) -> None:
    saved = []
    ask = Mock(return_value="请发正确的数字ID")
    router = make_router(store, ask=ask, saved=saved)
    assert router.dispatch(f"追踪术 小明 {wrong_id}", PRIVATE_CONTEXT) == "请发正确的数字ID"
    assert store.get_pending("c2c:alice", "alice") == ("追踪术", ["小明"])
    assert saved == []
    assert "可以看看" in router.dispatch("456", PRIVATE_CONTEXT)
    assert saved == [("小明", 456)]


def test_too_many_arguments_and_complete_parameter_replacement(store) -> None:
    saved = []
    router = make_router(store, saved=saved)
    assert "参数太多" in router.dispatch("追踪术 小明 123 多余", PRIVATE_CONTEXT)
    assert "dotaId" in router.dispatch("小明", PRIVATE_CONTEXT)
    assert "可以看看" in router.dispatch("小红 456", PRIVATE_CONTEXT)
    assert saved == [("小红", 456)]


def test_group_member_and_private_user_dialogues_are_isolated(store) -> None:
    router = make_router(store)
    alice = CommandContext(conversation_id="group:room", speaker_id="alice")
    bob = CommandContext(conversation_id="group:room", speaker_id="bob")
    other_group = CommandContext(conversation_id="group:other", speaker_id="alice")
    router.dispatch("今儿", alice)
    assert router.dispatch("小明", bob) == "AI:小明"
    assert router.dispatch("小明", other_group) == "AI:小明"
    assert router.dispatch("小明", PRIVATE_CONTEXT) == "AI:小明"
    assert router.dispatch("小明", alice) == "胜:2, 败:1"


def test_cancel_and_new_command_replace_pending_form(store) -> None:
    router = make_router(store)
    router.dispatch("追踪术", PRIVATE_CONTEXT)
    assert router.dispatch("取消", PRIVATE_CONTEXT) == "已取消这次参数填写。"
    assert router.dispatch("小明", PRIVATE_CONTEXT) == "AI:小明"
    router.dispatch("追踪术 小明", PRIVATE_CONTEXT)
    assert router.dispatch("今儿 小红", PRIVATE_CONTEXT) == "胜:2, 败:1"
    assert store.get_pending("c2c:alice", "alice") is None


@pytest.mark.parametrize("disabled,empty", [(False, False), (False, True), (True, False)])
def test_ai_failure_empty_answer_or_disabled_uses_working_fallback(store, disabled, empty) -> None:
    ask = Mock(return_value="" if empty else "AI问句")
    if not disabled and not empty:
        ask.side_effect = RuntimeError("AI unavailable")
    router = make_router(store, ask=ask, ai_enabled=not disabled)
    assert "请直接回复昵称" in router.dispatch("今儿", PRIVATE_CONTEXT)
    assert router.dispatch("小明", PRIVATE_CONTEXT) == "胜:2, 败:1"
    if disabled:
        ask.assert_not_called()


def test_valid_command_never_calls_ai_and_no_context_never_creates_shared_state(store) -> None:
    ask = Mock(return_value="问题")
    router = make_router(store, ask=ask)
    assert router.dispatch("今儿 小明", PRIVATE_CONTEXT) == "胜:2, 败:1"
    ask.assert_not_called()
    assert router.dispatch("今儿") == "问题"
    assert not list(store.root.iterdir())


@pytest.mark.parametrize("ai_available", [True, False])
def test_nickname_question_lists_current_bindings_even_when_ai_fails(store, ai_available) -> None:
    bindings = {"小明": 123, "小红": 456}
    ask = Mock(return_value="想查哪位选手？")
    if not ai_available:
        ask.side_effect = RuntimeError("AI unavailable")
    router = make_router(store, ask=ask, bindings=lambda: dict(bindings))
    question = router.dispatch("今儿", PRIVATE_CONTEXT)
    assert "小明（Dota ID：123）" in question
    assert "小红（Dota ID：456）" in question
    bindings["小明"] = 789
    question = router.dispatch("撒情况", PRIVATE_CONTEXT)
    assert "小明（Dota ID：789）" in question
    assert "Dota ID：123" not in question


def test_empty_permanent_roster_guides_tracking(store) -> None:
    router = make_router(store, bindings=lambda: {})
    assert "尚未记录选手，请先使用：追踪术 昵称 dotaId" in router.dispatch("今儿", PRIVATE_CONTEXT)
