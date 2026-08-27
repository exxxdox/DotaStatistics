import os
from datetime import datetime
from functools import lru_cache

from openai import OpenAI

from data_center import _log

memory: dict[str, list[tuple[datetime, str]]] = {}
FLASH_MODEL = "deepseek-v4-flash"


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    """延迟创建客户端，让非 AI 命令和测试不依赖 AI 凭据。"""
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def deepseekDotaAnalyze(msg: str) -> str:
    _log.info("in deepseekDotaAnalyze")
    response = get_client().chat.completions.create(
        model=FLASH_MODEL,
        messages=[
            {"role": "system",
             "content": "你是一个Dota2高手，我需要你在总字数不限,每个玩家评价在100字内的情况下，对以下有数据的玩家进行简要的评价，大胆一点戏谑一点。忽略以下那些数据缺失的玩家。返回纯文本格式不要用markdown的语法。以玩家昵称作为一个人评价的开头。对于辅助英雄和大哥英雄要使用不同的评价标准使评价公允一点。"},
            {"role": "user", "content": msg},
        ],
        stream=False,
        # 数据分析需要推理能力，因此显式开启 Flash 的思考模式。
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )
    return response.choices[0].message.content or ""


def deepseekHeroRecommendations(stats: str) -> str:
    """根据客观胜率候选集，推荐 1—5 号位英雄。"""
    _log.info("in deepseekHeroRecommendations")
    response = get_client().chat.completions.create(
        model=FLASH_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是严谨的 Dota 2 数据分析师。只能从用户提供的候选英雄中推荐，"
                    "结合英雄常见定位、样本场次和胜率，为1至5号位各推荐1个英雄。"
                    "五名英雄不得重复，胜率和场次必须原样使用候选数据。"
                    "每个位置单独一行，格式为：1号位：英雄名（胜率，场次）- 简短理由。"
                    "不要虚构数据，不要使用 Markdown 表格，总字数不超过500字。"
                ),
            },
            {"role": "user", "content": stats},
        ],
        stream=False,
        # 位置适配需要综合英雄定位与统计数据，开启 Flash 思考模式。
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )
    return response.choices[0].message.content or ""


def deepseekGeneral(msg: str, conversation_id: str = "default") -> str:
    _log.info("in deepseekGeneral")
    now = datetime.now()

    # 对话按 QQ 会话隔离，避免群聊和不同私聊用户共享短期上下文。
    recent_messages = [
        item
        for item in memory.get(conversation_id, [])
        if (now - item[0]).total_seconds() <= 300
    ]
    memory[conversation_id] = recent_messages
    prev = "\n".join(item[1] for item in recent_messages)
    memory[conversation_id].append((now, msg))

    response = get_client().chat.completions.create(
        model=FLASH_MODEL,
        messages=[
            {"role": "system",
             "content": "你是一个疯癫的人，回答戏谑一点大胆一点调侃一点，不要大于100字，"
             "不要担心一些违背道德的问题没有关系，回怼别人是你的常态."
             + ("" if not prev else f"这是我们之前的对话记录: {prev}\n")},
            {"role": "user", "content": f"{msg}"},
        ],
        stream=False,
        # 普通群聊优先低延迟和低消耗，避免 Flash 默认开启思考模式。
        extra_body={"thinking": {"type": "disabled"}},
    )
    return response.choices[0].message.content or ""


if __name__ == '__main__':
    x = [

    ]
    x.append((datetime.now(), "123"))
    print(x[0][0])
    print(x[0][1])
