# -*- coding: utf-8 -*-
import os
import re

import botpy
from botpy import logging
from botpy.ext.cog_yaml import read
from botpy.message import GroupMessage

from lib import open_dota_api
from data_center import name_id_ref, enable_ai, _log
from lib.deepseekapi import deepseekGeneral
from lib.open_dota_api import getPlayerWL
from lib.utils import SetDotaId, getDotaId
from service.today import todayAnalyze


class MyClient(botpy.Client):
    async def on_ready(self):
        _log.info(f"robot 「{self.robot.name}」 on_ready!")

    async def on_group_at_message_create(self, message: GroupMessage):
        word = message.content
        word_split = list(filter(None, word.split(" ")))
        _log.info(f"word_split 是:{word_split}")
        ret_mes = ""
        result_flag = False
        if re.match("^ *$", message.content):
            result_flag = True
            ret_mes = "\n指令列表:\n@我 追踪术 昵称 dotaId\n@我 撒情况 昵称\n@我 今儿 昵称\n@我 简报\n或者单纯的@我随便聊聊\n斗兽场中的选手是: "
            for target in name_id_ref:
                for k, v in target.items():
                    ret_mes += f"{k} "
        else:
            command = word_split[0]

            if command == "追踪术":
                if len(word_split) < 3:
                    pass
                else:
                    nick_name = word_split[1]
                    dota_id = int(word_split[2])
                    SetDotaId(nick_name, dota_id)
                    result_flag = True
                    _log.info(f"追踪术 {nick_name} {dota_id}")
                    ret_mes = "哦这个主意好,咱们可以看看这个逼最近打的怎么样~"
            if command == "撒情况":
                if len(word_split) < 2:
                    pass
                else:
                    nick_name = word_split[1]
                    dota_id = getDotaId(nick_name)
                    if dota_id is None:
                        pass
                    else:
                        result_flag = True
                        _log.info(f"撒情况: {nick_name}")
                        ret_mes = open_dota_api.getRecentMatchesApi(dota_id)
            if command == "今儿":
                if len(word_split) < 2:
                    pass
                else:
                    nick_name = word_split[1]
                    dota_id = getDotaId(nick_name)
                    if dota_id is None:
                        pass
                    else:
                        result_flag = True
                        _log.info(f"今儿: {nick_name}")
                        win, lose = getPlayerWL(dota_id, 1)
                        ret_mes = f"胜:{win}, 败:{lose}"
            if command == "简报":
                if len(word_split) != 1:
                    pass
                else:
                    result_flag = True
                    ret_mes = todayAnalyze()
        if not result_flag:
            if enable_ai:
                ret_mes = deepseekGeneral(word)
            else:
                ret_mes = "听不懂。"

        messageResult = await message._api.post_group_message(
            group_openid=message.group_openid,
            msg_type=0,
            msg_id=message.id,
            content=f"{ret_mes}")
        _log.info(messageResult)


def start():
    # 通过预设置的类型，设置需要监听的事件通道
    # intents = botpy.Intents.none()
    # intents.public_messages=True

    # 通过kwargs，设置需要监听的事件通道
    intents = botpy.Intents(public_messages=True)
    client = MyClient(intents=intents)
    client.run(appid=os.environ.get('QQBOT_APP_ID'), secret=os.environ.get('QQBOT_APP_SECRET'))


if __name__ == "__main__":
    pass
