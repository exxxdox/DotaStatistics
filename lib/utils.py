"""通用纯函数。选手/英雄映射已迁移至 lib.player_repository 与 lib.hero_name_resolver。"""

import re


def whetherWin(radiant_win: bool, slot: int) -> bool:
    # 两个条件相同即获胜，保留原先按玩家槽位判断阵营的行为。
    return bool(radiant_win) == (0 <= slot <= 127)


# QQ 单条消息正文上限约 2000 字符（中文比英文更紧凑，emoji 更占空间）。
# 留 200 字符余量，避免边界情况被 QQ 静默截断。
QQ_SAFE_MAX_CHARS = 1_800


def split_for_qq(text: str, max_chars: int = QQ_SAFE_MAX_CHARS) -> list[str]:
    """把长文本切分为不超过 max_chars 的段落列表，优先在句号/换行处切断。

    QQ 单条消息有长度上限；DeepSeek 分析回复（max_tokens=1200）可能超限。
    分段策略：
      1. 先按双换行切（段落边界）；
      2. 单段落过长时，在中文句号/感叹号/问号后切；
      3. 仍过长时按硬字符数切，尽量不切断标点。
    """
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    paragraphs: list[str] = []
    # 第一步：按段落分割（保留空行）。
    raw_parts = re.split(r"\n{2,}", text)
    buffer = ""
    for part in raw_parts:
        candidate = f"{buffer}\n\n{part}" if buffer else part
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        # 候选超限：先把 buffer 存起，再处理当前段落 part。
        if buffer:
            paragraphs.append(buffer.rstrip())
            buffer = ""
        if len(part) <= max_chars:
            paragraphs.append(part)
            continue
        # 单个段落也超限：在句号/感叹号/问号后切。
        sub_chunks = _soft_split(part, max_chars)
        for chunk in sub_chunks:
            paragraphs.append(chunk)

    if buffer:
        paragraphs.append(buffer.rstrip())

    return paragraphs or [text[:max_chars]]


def _soft_split(text: str, max_chars: int) -> list[str]:
    """硬切回退：在标点后优先切分，实在不行就按字符硬切。"""
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        # 在 max_chars 范围内找最后一个句号/感叹号/问号。
        window = remaining[:max_chars]
        # 匹配中文/英文句号、感叹号、问号、顿号、分号、冒号后加空白或行尾。
        cut_match = re.search(
            r"[。！？!?；;:…]\s*$|[。！？!?；;:…]\s*\n", window
        )
        if cut_match:
            cut_at = cut_match.end()
        else:
            # 次优：按空白切
            last_space = window.rfind(" ")
            cut_at = last_space if last_space > max_chars * 0.6 else max_chars
        chunks.append(remaining[:cut_at].rstrip())
        remaining = remaining[cut_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks

