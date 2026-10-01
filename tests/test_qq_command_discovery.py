import asyncio
import json
from typing import Any
from types import SimpleNamespace

import service.command_router as command_router
import service.qq_command_discovery as command_discovery
from service.commands import COMMANDS, Command

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
    private_items = build_private_menu()["menu"]["items"]
    private_messages = {
        item["send_message"]
        for item in private_items
        if item["type"] == "send_message"
    }
    help_item = next(item for item in private_items if item["name"] == "help")
    help_messages = {
        item["send_message"] for item in help_item["sub_menu_items"]
    }
    group_commands = {item["name"] for item in build_group_panel()["items"]}

    assert private_messages == {
        "clear",
        "高胜率英雄",
        "简报",
    }
    assert help_messages == {
        "追踪术",
        "撒情况",
    }
    assert group_commands == {
        "clear",
        "追踪术",
        "撒情况",
        "简报",
        "群OpenID",
        "高胜率英雄",
    }


def test_shared_definition_updates_menu_help_and_dispatch(monkeypatch) -> None:
    commands = (*COMMANDS, Command(
        "新简报", "新简报", "新的简报入口", handler="_report", private_label="新简报",
    ))
    monkeypatch.setattr(command_router, "COMMANDS", commands)
    monkeypatch.setattr(command_discovery, "COMMANDS", commands)
    router = command_router.CommandRouter(SimpleNamespace(
        list_player_nicknames=lambda: [], get_today_report=lambda: "比赛简报",
    ), ai_enabled=False)

    assert router.dispatch("新简报") == "比赛简报"
    assert [line for line in router.dispatch("").splitlines() if line.startswith("@我 ")] == [
        f"@我 {command.usage}" for command in commands
    ]
    assert build_group_panel()["items"][-1] == {
        "type": "command", "name": "新简报", "desc": "新的简报入口", "only_admin": False,
    }
    assert build_private_menu()["menu"]["items"][1] == {
        "name": "新简报", "type": "send_message", "send_message": "新简报",
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


def test_configure_accepts_wrapped_empty_panel_response() -> None:
    calls: list[tuple[str, str]] = []

    async def request(route, **_kwargs):
        calls.append((route.method, route.path))
        if route.method == "GET":
            return {"code": 0, "data": {"is_end": True, "next_cursor": ""}}
        return {}

    asyncio.run(QQCommandDiscoveryService(request).configure())

    assert calls[-1] == ("POST", "/v2/panels")
