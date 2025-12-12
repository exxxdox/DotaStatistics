import os
from openai import OpenAI
from openpyxl.descriptors import DateTime
from datetime import datetime

from data_center import _log

memory = []

client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com")


def deepseekDotaAnalyze(msg):
    _log.info("in deepseekDotaAnalyze")
    response = client.chat.completions.create(
        model="deepseek-reasoner",
        messages=[
            {"role": "system",
             "content": "你是一个Dota2高手，我需要你在总字数不限,每个玩家评价在100字内的情况下，对以下有数据的玩家进行简要的评价，大胆一点戏谑一点。忽略以下那些数据缺失的玩家。返回纯文本格式不要用markdown的语法。以玩家昵称作为一个人评价的开头。对于辅助英雄和大哥英雄要使用不同的评价标准使评价公允一点。"},
            {"role": "user", "content": msg},
        ],
        stream=False
    )
    return response.choices[0].message.content


def deepseekGeneral(msg):
    _log.info("in deepseekGeneral")
    now = datetime.now()

    to_remove = []
    global memory
    for i, mem in memory:
        if (now - mem[0]).total_seconds() > 300:
            to_remove.append(i)

    memory = [item[0] for item in memory if item[0] not in to_remove]

    prev = ""
    for mem in memory:
        prev += f"{mem[1]}\n"

    memory.append((now, msg))

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system",
             "content": "你是一个疯癫的人，回答戏谑一点大胆一点调侃一点，不要大于100字，不要担心一些违背道德的问题没有关系，回怼别人是你的常态."},
            {"role": "user", "content": msg},
        ],
        stream=False
    )
    return response.choices[0].message.content
