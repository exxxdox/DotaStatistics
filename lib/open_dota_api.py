from re import match

import requests

from data_center import heros, _log
from lib.utils import whetherWin, getHeroName, readHeroNameFromExcelConfig, getDotaNameById, init_name_id_ref

matches_num_limit = 3


def getPlayerWL(id, day_offset):
    response = requests.get(f"https://api.opendota.com/api/players/{id}/wl?game_mode=22&date={day_offset}")
    if response.status_code == 200:
        data = response.json()
        win = data["win"]
        lost = data["lose"]
        return win, lost
    else:
        _log.error(f"Failed to retrieve getHeroId. Status code: {response.status_code}")


def getPlayer(id):  # todo
    response = requests.get(f"https://api.opendota.com/api/players/{id}")
    if response.status_code == 200:
        data = response.json()
        return {"picture": data["profile"]["avatar"]}
    else:
        _log.error(f"Failed to retrieve getHeroId. Status code: {response.status_code}")


def getHeroEnNameApi():
    response = requests.get("https://api.opendota.com/api/heroes")
    if response.status_code == 200:
        # 解析JSON响应
        data = response.json()
        for hero in data:
            heros.append({"id": hero.get('id'), "name": hero.get('name')})
    else:
        _log.error(f"Failed to retrieve getHeroEnNameApi. Status code: {response.status_code}")


def getRecentMatchesApi(id):
    response = requests.get(f"https://api.opendota.com/api/players/{id}/recentMatches")
    if response.status_code == 200:
        result = "\n"
        # 解析JSON响应
        data = response.json()
        size = 0
        for i, game in enumerate(data):
            if game.get('game_mode') != 22:
                continue
            radiant_win = game.get('radiant_win')
            player_slot = game.get('player_slot')
            mr_lin = "哦嘎嘎嘎嘎暴利,赢了一把." if whetherWin(radiant_win, player_slot) else "曾恶心啊,尽力了."
            hero_id = game.get('hero_id')
            hero_damage = game.get('hero_damage')
            hero_healing = game.get('hero_healing')
            deaths = game.get('deaths')
            kills = game.get('kills')
            assists = game.get('assists')
            gold_per_min = game.get('gold_per_min')
            line = f"{mr_lin} 英雄:{getHeroName(hero_id)} 伤害:{hero_damage} 杀:{kills} 死:{deaths} 助攻:{assists} gpm:{gold_per_min}\n"
            result += line
            size += 1
            if size >= matches_num_limit:
                break

        return result
    else:
        _log.error(f"Failed to retrieve getRecentMatchesApi. Status code: {response.status_code}")


def getMatchApi(match_id, account_id):
    response = requests.get(f"https://api.opendota.com/api/matches/{match_id}")
    result = ""
    if response.status_code == 200:
        data = response.json()
        duration = data.get("duration")  # seconds
        result += f" 持续时间{duration}秒 "
        for player in data.get("players"):
            if player.get("account_id") == account_id:
                isRadiant = player.get("isRadiant")
                gold_per_min = player.get("gold_per_min")
                xp_per_min = player.get("xp_per_min")
                hero_damage = player.get("hero_damage")
                tower_damage = player.get("tower_damage")
                hero_healing = player.get("hero_healing")
                total_gold = player.get("total_gold")
                total_xp = player.get("total_xp")
                result += f" {"天辉" if isRadiant else "夜魇"} gpm {gold_per_min} xpm {xp_per_min} 对英雄伤害{hero_damage} 对英雄治疗{hero_healing} 建筑伤害{tower_damage} 总金钱{total_gold} 总经验 {total_xp} "
    else:
        _log.error(f"Failed to retrieve getMatchApi. Status code: {response.status_code}")
    return result


def getMatchesByIdApi(account_id, date):
    response = requests.get(f"https://api.opendota.com/api/players/{account_id}/matches?date={date}")
    result = f""
    if response.status_code == 200:
        _log.info(f"getMatchApi 成功获取 {getDotaNameById(account_id)}")
        # 解析JSON响应
        data = response.json()
        for i, game in enumerate(data):
            if game.get('game_mode') != 22:
                continue
            match_id = game.get('match_id')
            radiant_win = game.get('radiant_win')
            player_slot = game.get('player_slot')
            mr_lin = "游戏" + ("胜利" if whetherWin(radiant_win, player_slot) else "失败")
            hero_id = game.get('hero_id')
            deaths = game.get('deaths')
            kills = game.get('kills')
            assists = game.get('assists')
            line = f"{mr_lin} 英雄:{getHeroName(hero_id)} 击杀:{kills} 死亡:{deaths} 助攻:{assists} {getMatchApi(match_id, account_id)}\n"
            result += line
        if result == "":
            _log.info(f"{getDotaNameById(account_id)} 数据为空")
            return ""
        else:
            result += f"此玩家数据结束\n"
        return result
    else:
        _log.error(f"Failed to retrieve getRecentMatchesApi. Status code: {response.status_code}")


if __name__ == '__main__':
    init_name_id_ref()
    readHeroNameFromExcelConfig()

    # _log.info(getMatchesByIdApi(id, 1))
