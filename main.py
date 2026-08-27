"""DotaStatistics 入口：初始化选手与英雄引用后启动 QQ 机器人。"""

from lib import open_dota_api
import qq_bot
from lib.utils import init_name_id_ref, readHeroNameFromExcelConfig


def init():
    init_name_id_ref()
    readHeroNameFromExcelConfig()
    open_dota_api.getHeroEnNameApi()


if __name__ == '__main__':
    init()
    qq_bot.start()
