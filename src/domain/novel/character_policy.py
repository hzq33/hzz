"""角色规则单一事实源 — 身份词 / 作家表 / 称谓 / 名字归一。

此前规则散落 5+ 处（validate._KNOWN_WRITERS / harvest._OCCUPATION_NOISE /
character_ner._HONOR / dialogue_quota.normalize / config.importance_blacklist），
维护时互相不一致（例：作家表漏"太宰"碎片导致角色表污染）。
全部收敛到此模块，各处只做 import。

⚠️ 注意：dialogue_span.is_noise_speaker 的 _NOISE 表是「词法碎片」判定
（代词/虚词），与这里的「角色规则」（作家/身份词）语义不同，保持独立。
"""

from __future__ import annotations


# ── 作家 / 历史名人（LLM 容易遗漏的，作为提示词参考而非穷举名单）────
# 含常用碎片（"太宰"、"三岛"）—— 角色表里出现这些短名同样要剔除
KNOWN_WRITERS = {
    "太宰治", "三岛由纪夫", "川端康成", "村上春树", "夏目漱石", "芥川龙之介",
    "雨森", "岩浅氏", "小村雏", "小田和正", "拿破仑", "赫敏", "麦凯恩",
    "三岛", "川端", "芥川", "太宰", "村上", "夏目", "太宰治", "三岛由纪夫",
}

# ── 常见身份词（不是角色名；除非是作品中的唯一指代）────────────────
ROLE_WORDS = {
    "部长", "店员", "老师", "学姐", "学长", "前辈", "后辈", "会长", "书记",
    "班主任", "校长", "医生", "护士", "学生", "同学", "女生", "男生",
    "女孩", "男孩", "少年", "少女", "主角", "配角", "路人", "经理", "老板",
    "店主", "服务员", "司机", "警察", "军人", "村民", "镇长", "国王", "女王",
    "公主", "王子", "商人", "女部员", "部员", "女学生", "女学生们", "男学生",
    "男学生们", "学生们", "图书管理员", "众人", "大家",
}

# ── 称谓后缀（剥离后得到纯名；"利姆露大人"→"利姆露"）────────────────
HONOR_SUFFIXES = ("同学", "小姐", "先生", "大人", "桑", "君", "酱", "醬", "老师", "前辈")

# ── 常用译名归一（说话人/角色名 → 主流译名）──────────────────
# 同一外文名存在多种中文音译（如 Rimuru → 利姆路/利姆露）。
# 以主流/官方译名为 canonical，其余归一到它；原始名会作为 alias 保留，
# 检索时两个译名都能命中。
TRANSLATION_ALIASES = {
    "利姆路": "利姆露",  # Rimuru
    # 其他作品按需追加，如：
    # "维鲁多拉": "维尔德拉",  # Velzard/Veldora 类
}


def apply_translation_alias(name: str) -> str:
    """常用译名归一到主流译名（未命中返回原名）。"""
    s = (name or "").strip()
    return TRANSLATION_ALIASES.get(s, s)


def normalize_character_name(name: str) -> str:
    """Strip NER debris (trailing middle-dot / separators) from person names."""
    s = (name or "").strip()
    while s and s[-1] in "・·．.、,，/／":
        s = s[:-1].rstrip()
    return s.strip()


def strip_honor(name: str) -> str:
    """去称谓后缀（保留 ≥2 字核心）。"""
    s = (name or "").strip()
    for h in HONOR_SUFFIXES:
        if s.endswith(h) and len(s) > len(h) + 1:
            return s[: -len(h)]
    return s


def is_noise_name(name: str) -> bool:
    """角色规则级噪声：作家名 / 身份词 / 单字 / 占位。"""
    s = normalize_character_name(name)
    if not s:
        return True
    if len(s) < 2:
        return True
    if s in KNOWN_WRITERS:
        return True
    if s in ROLE_WORDS:
        return True
    if s in ("史莱姆", "哥布林", "人类", "兽人", "魔人", "魔物", "精灵", "矮人", "恶魔", "天使"):
        return True
    return False
