import json
from collections.abc import Awaitable, Callable
from typing import Any

from botpy.http import Route

from service.commands import COMMANDS

RequestCallable = Callable[..., Awaitable[Any]]
GROUP_PANEL_REMARK = "DotaStatistics group commands"


def build_private_menu() -> dict[str, Any]:
    """从共享定义生成菜单，按钮只发送命令名以触发参数追问。"""
    help_items = [
        {"name": command.private_label, "type": "send_message", "send_message": command.name}
        for command in COMMANDS if command.private_in_help
    ]
    # QQ 既有菜单顶层按英雄榜、简报排列，群面板和帮助维持命令表顺序。
    items = [
        {"name": command.private_label, "type": "send_message", "send_message": command.name}
        for command in reversed(COMMANDS)
        if command.private_label and not command.private_in_help
    ]
    return {"menu": {"items": [
        {"name": "help", "type": "menu", "sub_menu_items": help_items},
        *items,
    ]}}


def build_group_panel() -> dict[str, Any]:
    """共享命令说明和群别名，保持 QQ 指令面板与帮助一致。"""
    return {
        "items": [
            {
                "type": "command",
                "name": command.group_alias or command.name,
                "desc": command.description,
                "only_admin": False,
            }
            for command in COMMANDS
        ],
        "remark": GROUP_PANEL_REMARK,
    }


class QQCommandDiscoveryService:
    """通过 QQ 新版接口同步单聊菜单和群聊指令面板。"""

    def __init__(self, request: RequestCallable) -> None:
        # qq-botpy 1.2 尚未封装菜单接口，因此复用其已鉴权的底层请求器。
        self._request = request

    async def configure(self) -> None:
        await self._request(Route("PUT", "/v2/menu"), json=build_private_menu())
        await self._upsert_group_panel()

    async def _upsert_group_panel(self) -> None:
        response = await self._request(
            Route("GET", "/v2/panels"), params={"scope": "group", "limit": 50}
        )
        records = self._extract_panel_records(response)

        panel = build_group_panel()
        existing = next(
            (
                record
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get("panel"), dict)
                and record["panel"].get("remark") == GROUP_PANEL_REMARK
            ),
            None,
        )
        if existing is not None and existing.get("panel_id"):
            await self._request(
                Route("PUT", f"/v2/panels/{existing['panel_id']}"),
                json={"panel": panel},
            )
            return

        await self._request(
            Route("POST", "/v2/panels"),
            json={"scope": "group", "target_type": "all", "panel": panel},
        )

    @staticmethod
    def _extract_panel_records(response: Any) -> list[dict[str, Any]]:
        """兼容 qq-botpy 原始 JSON、data 包装和空列表字段省略。"""
        if isinstance(response, str):
            try:
                # qq-botpy 对带 charset 的 JSON 响应可能返回原始字符串。
                response = json.loads(response)
            except json.JSONDecodeError as error:
                raise RuntimeError("QQ 指令面板列表返回了无效 JSON") from error

        if not isinstance(response, dict):
            raise RuntimeError(
                f"QQ 指令面板列表响应格式错误: {type(response).__name__}"
            )

        code = response.get("code")
        if code not in (None, 0, "0"):
            # 只记录错误码，避免把服务端返回内容或标识符带入异常日志。
            raise RuntimeError(f"QQ 指令面板接口业务错误: code={code}")

        payload = response.get("data", response)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"QQ 指令面板 data 响应格式错误: {type(payload).__name__}"
            )

        records = payload.get("records")
        if records is None and (
            not payload or "is_end" in payload or "next_cursor" in payload
        ):
            # 实际接口在首次查询且没有面板时可能省略空 records 字段。
            return []
        if not isinstance(records, list):
            keys = ",".join(sorted(str(key) for key in payload))
            raise RuntimeError(
                "QQ 指令面板列表响应格式错误: "
                f"records={type(records).__name__}, keys={keys}"
            )
        return records
