"""命令解析与分发；依赖以可调用对象注入，收发消息由 QQ 适配层负责。"""

from collections.abc import Callable
from dataclasses import dataclass

from data_center import _log, enable_ai
from lib.conversation_memory import ConversationMemory, get_memory_store

CommandHandler = Callable[[list[str]], str]


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
    history_before_id: int | None = None
    speaker_id: str | None = None


@dataclass(frozen=True)
class BotServices:
    """集中声明外部依赖，避免命令解析与网络、存储实现强耦合。"""

    set_dota_id: Callable[[str, int], None]
    get_dota_id: Callable[[str], int | None]
    get_recent_matches: Callable[[int], str | None]
    get_today_report: Callable[[], str]
    chat: Callable[[str, str, int | None], str]
    resolve_hero_name: Callable[[int], str | None]
    list_player_nicknames: Callable[[], list[str]]
    ask_command_parameter: Callable[[str, str, str], str] | None = None
    list_player_bindings: Callable[[], dict[str, int]] | None = None


class CommandRouter:
    """负责命令解析和分发；SDK 回调只处理异步收发消息。"""

    def __init__(
        self,
        services: BotServices | None = None,
        ai_enabled: bool = enable_ai,
        memory_store: ConversationMemory | None = None,
    ) -> None:
        if services is None:
            # 仅默认启动时加载真实集成，让注入替身的路由不依赖网络和文件加载。
            from bootstrap import build_default_services

            services = build_default_services()
        self.services = services
        self.ai_enabled = ai_enabled
        self.memory_store = memory_store
        self._commands: dict[str, CommandHandler] = {
            "追踪术": self._track,
            "撒情况": self._recent_matches,
            "简报": self._report,
        }

    def dispatch(self, content: str, context: CommandContext | None = None) -> str:
        normalized_content = normalize_command_content(content)
        words = normalized_content.split()
        # 正文只进入压缩数据库，避免普通日志额外保留未压缩的聊天记录。
        _log.info("收到消息，开始命令分发")
        dialogue = self._dialogue_store(context)
        pending = dialogue.get_pending(context.conversation_id, context.speaker_id) if dialogue else None

        # 升级后清理已删除命令的旧追问，避免恢复状态时调用不存在的处理器。
        if pending and pending[0] not in self._commands:
            dialogue.set_pending(context.conversation_id, context.speaker_id, None)
            pending = None

        if pending and normalized_content in {"取消", "退出"}:
            dialogue.set_pending(context.conversation_id, context.speaker_id, None)
            return "已取消这次参数填写。"

        if not words:
            if pending:
                return self._parameter_command(*pending, context)
            return self._help()

        if words[0].casefold() in {
            "查看当前群openid".casefold(),
            "群openid".casefold(),
        }:
            if dialogue:
                dialogue.set_pending(context.conversation_id, context.speaker_id, None)
            return self._show_group_openid(words[1:], context)

        handler = self._commands.get(words[0])
        if handler is not None:
            if dialogue:
                # 新命令替换旧追问，避免用户被之前的参数填写流程困住。
                dialogue.set_pending(context.conversation_id, context.speaker_id, None)
            if words[0] in {"追踪术", "撒情况"}:
                return self._parameter_command(words[0], words[1:], context)
            return handler(words[1:])
        if pending:
            command, saved_args = pending
            expected_count = 2 if command == "追踪术" else 1
            # 支持只补下一个参数，也允许直接重发一整组参数修正昵称。
            args = words if len(words) == expected_count else saved_args + words
            return self._parameter_command(command, args, context)
        conversation_id = (
            context.conversation_id
            if context is not None and context.conversation_id
            else (
                f"group:{context.group_openid}"
                if context is not None and context.group_openid
                else "default"
            )
        )
        return self.chat(
            content,
            conversation_id,
            context.history_before_id if context is not None else None,
        )

    def _dialogue_store(self, context: CommandContext | None) -> ConversationMemory | None:
        if context is None or not context.conversation_id or not context.speaker_id:
            return None
        # 仅参数追问需要状态；存储对象没有常驻对话缓存。
        return self.memory_store or get_memory_store()

    def _parameter_command(
        self, command: str, args: list[str], context: CommandContext | None
    ) -> str:
        expected_count = 2 if command == "追踪术" else 1
        if args in (["昵称"], ["昵称", "dotaId"]):
            args = []
        reason = ""
        if len(args) > expected_count:
            args = []
            reason = "参数太多，请一次发送一个昵称。"
        if args and len(args[0]) > 256:
            args = []
            reason = "昵称不能超过256个字符。"
        field = "昵称"
        if args:
            field = "dotaId"
            if command == "追踪术" and len(args) == 2:
                try:
                    valid_id = 0 < int(args[1]) and len(args[1]) <= 256
                except ValueError:
                    valid_id = False
                if not valid_id:
                    args = args[:1]
                    reason = "dotaId 必须是正整数。"
        dialogue = self._dialogue_store(context)
        if len(args) == expected_count:
            # 先清状态，再执行业务；查询或写入失败不能把下一条聊天当旧参数。
            if dialogue:
                dialogue.set_pending(context.conversation_id, context.speaker_id, None)
            return self._commands[command](args)
        if dialogue:
            dialogue.set_pending(context.conversation_id, context.speaker_id, (command, args))
        fallback = f"{reason}请直接回复{field}，或发送完整命令。回复“取消”可退出。"
        roster_hint = ""
        if field == "昵称" and self.services.list_player_bindings is not None:
            bindings = self.services.list_player_bindings()
            # 名单由代码展示，AI漏报或不可用时也能准确提示所有已绑定选手。
            roster_hint = (
                "\n已记录选手：" + "、".join(
                    f"{nickname}（Dota ID：{dota_id}）"
                    for nickname, dota_id in bindings.items()
                )
                if bindings else "\n尚未记录选手，请先使用：追踪术 昵称 dotaId。"
            )
        if self.ai_enabled and self.services.ask_command_parameter is not None:
            try:
                question = self.services.ask_command_parameter(command, field, reason).strip()
                if question:
                    return question + roster_hint
            except Exception:
                # AI 只负责问句，故障时仍能按确定的参数状态完成填写。
                _log.exception("生成命令参数追问失败")
        return fallback + roster_hint

    def chat(
        self, content: str, conversation_id: str, history_before_id: int | None = None
    ) -> str:
        """直接处理 AI 对话，不让私聊内容误入群命令路由。"""
        return (
            self.services.chat(content, conversation_id, history_before_id)
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
            "@我 简报\n"
            "@我 查看当前群OpenID\n"
            "@我 高胜率英雄\n"
            "或者单纯地@我随便聊聊\n"
            f"斗兽场中的选手是: {players}"
        )

    def _track(self, args: list[str]) -> str:
        # 参数数量、占位词和正整数已由统一入口校验，业务处理只负责绑定。
        nickname, raw_dota_id = args
        self.services.set_dota_id(nickname, int(raw_dota_id))
        _log.info("追踪关系已更新")
        return "哦这个主意好,咱们可以看看这个逼最近打的怎么样~"

    def _recent_matches(self, args: list[str]) -> str:
        # 输入格式已由统一入口校验；是否已绑定仍需查询当前仓库。
        nickname = args[0]
        dota_id = self.services.get_dota_id(nickname)
        if dota_id is None:
            return f"还没有追踪选手「{nickname}」。"

        _log.info("查询近期比赛")
        result = self.services.get_recent_matches(dota_id)
        return result or "暂时没有查到近期比赛。"

    def _report(self, args: list[str]) -> str:
        if args:
            return "用法: 简报"
        return self.services.get_today_report()
