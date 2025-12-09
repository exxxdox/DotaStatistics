from data_center import heros_name, heros, name_id_ref, common_id_path, hero_excel_path, _log
from lib import open_dota_api
from lib.deepseekapi import deepseekDotaAnalyze
from lib.open_dota_api import getMatchesByIdApi
from lib.utils import init_name_id_ref, readHeroNameFromExcelConfig


def todayAnalyze():
    result = f"根据距今{24}小时的数据分析\n"
    ai_request_str = ""
    for ref in name_id_ref:
        for key in ref.keys():
            name = key
            id = ref[key]
            recent_matches = getMatchesByIdApi(id, 1)
            if recent_matches != '':
                ai_request_str += f"{name}，id为{id} 的近期数据是\n{recent_matches}"
    # _log.info(f"dota数据获取结果:\n{ai_request_str}\n")
    result += deepseekDotaAnalyze(ai_request_str)
    return result


if __name__ == '__main__':
    init_name_id_ref()
    readHeroNameFromExcelConfig()
    open_dota_api.getHeroEnNameApi()
    _log.info(todayAnalyze())
