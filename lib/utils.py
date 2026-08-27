"""通用纯函数。选手/英雄映射已迁移至 lib.player_repository 与 lib.hero_name_resolver。"""


def whetherWin(radiant_win, slot):
    if radiant_win:
        if 0 <= slot <= 127:
            return True
        else:
            return False
    else:
        if 0 <= slot <= 127:
            return False
        else:
            return True
