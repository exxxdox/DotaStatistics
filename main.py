# This is a sample Python script.

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
