from collections.abc import Awaitable, Callable
from typing import Any

from botpy.http import Route

RequestCallable = Callable[..., Awaitable[Any]]
GROUP_PANEL_REMARK = "DotaStatistics group commands"


def build_private_menu() -> dict[str, Any]:
    """生成单聊全局菜单；按钮名称遵守 QQ 的五个中文字符限制。"""
    return {
        "menu": {
            "items": [
                {
                    "name": "英雄胜率",
                    "type": "send_message",
                    "send_message": "高胜率英雄",
                },
                {
                    "name": "追踪选手",
                    "type": "send_message",
                    "send_message": "追踪术 昵称 dotaId",
                },
                {
                    "name": "近期比赛",
                    "type": "send_message",
                    "send_message": "撒情况 昵称",
                },
                {
                    "name": "今日战绩",
                    "type": "send_message",
                    "send_message": "今儿 昵称",
                },
                {
                    "name": "今日简报",
                    "type": "send_message",
                    "send_message": "简报",
                },
            ]
        }
    }


def build_group_panel() -> dict[str, Any]:
    """生成所有群可见的指令面板，名称即用户点击后填入的指令。"""
    return {
        "items": [
            {
                "type": "command",
                "name": "追踪术",
                "desc": "绑定昵称和Dota ID",
                "only_admin": False,
            },
            {
                "type": "command",
                "name": "撒情况",
                "desc": "查询选手近期比赛",
                "only_admin": False,
            },
            {
                "type": "command",
                "name": "今儿",
                "desc": "查询选手今日战绩",
                "only_admin": False,
            },
            {
                "type": "command",
                "name": "简报",
                "desc": "生成今日比赛简报",
                "only_admin": False,
            },
            {
                "type": "command",
                "name": "群OpenID",
                "desc": "查看当前群OpenID",
                "only_admin": False,
            },
            {
                "type": "command",
                "name": "测试胜率榜",
                "desc": "测试定时英雄榜单",
                "only_admin": False,
            },
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
        if not isinstance(response, dict) or not isinstance(
            response.get("records"), list
        ):
            # 查询失败时禁止盲目创建，避免每次重启产生重复面板。
            raise RuntimeError("QQ 指令面板列表响应格式错误")

        panel = build_group_panel()
        existing = next(
            (
                record
                for record in response["records"]
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
