"""通用纯函数。选手/英雄映射已迁移至 lib.player_repository 与 lib.hero_name_resolver。"""


def whetherWin(radiant_win: bool, slot: int) -> bool:
    # 两个条件相同即获胜，保留原先按玩家槽位判断阵营的行为。
    return bool(radiant_win) == (0 <= slot <= 127)
