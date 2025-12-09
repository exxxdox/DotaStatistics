import json
import os

import pandas as pd

from data_center import heros_name, heros, name_id_ref, common_id_path, hero_excel_path, _log


def getDotaNameById(dota_id):
    for target in name_id_ref:
        for k, v in target.items():
            if v == dota_id:
                return k


def SetDotaId(nick_name, dota_id):
    flag = True
    for target in name_id_ref:
        if target.get(nick_name, None) == nick_name:
            target[nick_name] = dota_id
            flag = False
    if flag:
        name_id_ref.append({nick_name: dota_id})
    write_name_id_ref(nick_name, dota_id)


def getDotaId(nick_name):
    for target in name_id_ref:
        for k, v in target.items():
            if k == nick_name:
                return v

    return None


def getHeroNameZH(id):
    for hero in heros_name:
        for k, v in hero.items():
            if k == id:
                return v
    return None


def getHeroName(id):
    name = getHeroNameZH(id)
    if name is None:
        for hero in heros:
            if hero.get('id') == id:
                return hero.get('name')
    else:
        return name


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


def init_name_id_ref():
    create_json_file_if_not_exists(common_id_path)
    # 打开并读取 JSON 文件
    with open(common_id_path, 'r') as file:
        data = json.load(file)
        for obj in data:
            SetDotaId(obj["nick_name"], obj["dota_id"])


def readHeroNameFromExcelConfig():
    # 读取 Excel 文件
    df = pd.read_excel(hero_excel_path)
    # 遍历每一行
    for index, row in df.iterrows():
        heros_name.append({row['id']: row['name_zh']})


def write_name_id_ref(nick_name, dota_id):
    exist_flag = False
    with open(common_id_path, 'r') as file:
        data = json.load(file)
        for obj in data:
            if obj["nick_name"] == nick_name:
                obj["dota_id"] = dota_id
                exist_flag = True
        if not exist_flag:
            data.append({"nick_name": nick_name, "dota_id": dota_id})

    with open(common_id_path, 'w') as file:
        json.dump(data, file, indent=4)


def create_json_file_if_not_exists(file_path, default_data=None):
    """
    如果 JSON 文件不存在则创建

    Args:
        file_path: 文件路径
        default_data: 默认数据
    """
    if default_data is None:
        default_data = []

    # 检查文件是否存在
    if not os.path.exists(file_path):
        _log.info(f"文件 {file_path} 不存在，正在创建...")

        # 写入默认数据
        try:
            with open(file_path, 'w', encoding='utf-8') as file:
                json.dump(default_data, file, indent=4)
            _log.info(f"JSON文件已创建: {file_path}")
            return True
        except Exception as e:
            _log.info(f"创建JSON文件失败: {e}")
            return False
    else:
        _log.info(f"文件 {file_path} 已存在")
        return True


if __name__ == '__main__':
    readHeroNameFromExcelConfig()
    print(heros_name)
