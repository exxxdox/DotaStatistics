"""共享用户可见命令资料，避免路由、帮助和 QQ 菜单各自维护名单。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    usage: str
    description: str
    handler: str | None = None
    group_alias: str | None = None
    private_label: str | None = None
    private_in_help: bool = False


TRACK_COMMAND = Command(
    "追踪术", "追踪术 昵称 dotaId", "绑定昵称和Dota ID",
    handler="_track", private_label="追踪术", private_in_help=True,
)
RECENT_COMMAND = Command(
    "撒情况", "撒情况 昵称", "查询选手最近5场天梯比赛",
    handler="_recent_matches", private_label="撒情况", private_in_help=True,
)
REPORT_COMMAND = Command(
    "简报", "简报", "生成今日比赛简报",
    handler="_report", private_label="今日简报",
)
GROUP_OPENID_COMMAND = Command(
    "查看当前群OpenID", "查看当前群OpenID", "查看当前群OpenID",
    group_alias="群OpenID",
)
HERO_COMMAND = Command(
    "高胜率英雄", "高胜率英雄", "查询近期英雄胜率榜",
    private_label="英雄胜率",
)

CLEAR_COMMAND = Command(
    "clear", "clear", "清空当前会话记录", private_label="清空对话",
)

COMMANDS = (
    TRACK_COMMAND, RECENT_COMMAND, REPORT_COMMAND, GROUP_OPENID_COMMAND, HERO_COMMAND,
    CLEAR_COMMAND,
)
