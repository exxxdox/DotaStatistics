import asyncio
import json
from typing import Any

from service.qq_command_discovery import (
    GROUP_PANEL_REMARK,
    QQCommandDiscoveryService,
    build_group_panel,
    build_private_menu,
)


class FakeRequest:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, route, **kwargs):
        self.calls.append((route.method, route.path, kwargs))
        if route.method == "GET":
            return {"records": self.records, "next_cursor": "", "is_end": True}
        return {}


def test_payloads_expose_supported_private_and_group_commands() -> None:
    private_messages = {
        item["send_message"] for item in build_private_menu()["menu"]["items"]
    }
    group_commands = {item["name"] for item in build_group_panel()["items"]}

    assert private_messages == {
        "高胜率英雄",
        "追踪术 昵称 dotaId",
        "撒情况 昵称",
        "今儿 昵称",
        "简报",
    }
    assert group_commands == {
        "追踪术",
        "撒情况",
        "今儿",
        "简报",
        "群OpenID",
        "测试胜率榜",
    }


def test_configure_creates_group_panel_when_missing() -> None:
    request = FakeRequest(records=[])

    asyncio.run(QQCommandDiscoveryService(request).configure())

    assert [call[:2] for call in request.calls] == [
        ("PUT", "/v2/menu"),
        ("GET", "/v2/panels"),
        ("POST", "/v2/panels"),
    ]
    assert request.calls[2][2]["json"]["target_type"] == "all"


def test_configure_updates_existing_group_panel() -> None:
    request = FakeRequest(
        records=[
            {
                "panel_id": "panel-id",
                "panel": {"remark": GROUP_PANEL_REMARK},
            }
        ]
    )

    asyncio.run(QQCommandDiscoveryService(request).configure())

    assert [call[:2] for call in request.calls] == [
        ("PUT", "/v2/menu"),
        ("GET", "/v2/panels"),
        ("PUT", "/v2/panels/panel-id"),
    ]


def test_configure_accepts_json_string_returned_by_qq_botpy() -> None:
    calls: list[tuple[str, str]] = []

    async def request(route, **_kwargs):
        calls.append((route.method, route.path))
        if route.method == "GET":
            # SDK 在 content-type 包含 charset 时会保留 JSON 原文。
            return json.dumps({"records": [], "next_cursor": "", "is_end": True})
        return "{}"

    asyncio.run(QQCommandDiscoveryService(request).configure())

    assert calls == [
        ("PUT", "/v2/menu"),
        ("GET", "/v2/panels"),
        ("POST", "/v2/panels"),
    ]
