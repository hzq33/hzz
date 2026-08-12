"""章节检测 prompt 的结构化拼接模块。

将单段大 prompt 拆分为 7 个独立模块，用 dataclass 封装可配置项，
其余作为常量字符串。提供 build_chapter_prompt() 函数做拼接。

设计目标：
- 可读性：每个模块独立，便于审查与修改
- 可配置：语言/严格度/章节类型可通过 spec 覆盖
- 可测试：每个模块可单独单元测试
- 可扩展：新增 few-shot 示例或章节类型只需改常量列表
"""

from __future__ import annotations

from dataclasses import dataclass

# ============================================================================
# Module 1: Role & Task
# ============================================================================

ROLE_TASK = """你是小说结构分析专家。你的任务是分析小说样本，识别**章节级分节标记**与**文档结构特征**，输出一个 Python 正则表达式和一组结构规则。"""


# ============================================================================
# Module 2: Chapter Types (chapter-level markers to match)
# ============================================================================

# 5 大类，每类一个 (类别名, 关键词列表) 元组
CHAPTER_TYPE_GROUPS: list[tuple[str, list[str]]] = [
    ("起始类", [
        "序", "序章", "序言", "序幕", "楔子", "引子", "前言", "卷首", "开篇", "Prologue",
    ]),
    ("正文章节", [
        "第N章", "第N回", "第N节", "第N卷", "第N篇", "第N话", "第N話", "第N败", "第N胜", "第N幕", "Chapter N",
    ]),
    ("装饰型", [
        "被 ~ ◆ ◇ ▲ ■ ○ ● △ ▽ 等符号包裹的章节标记（如 ~第N话~、◆第N章◆）",
    ]),
    ("编号型", [
        "数字序号（一、/1./（一）/第一章）+ 章节标题",
    ]),
    ("结束类", [
        "尾声", "终章", "结章", "Fin", "Epilogue",
    ]),
    ("附录类", [
        "后记", "跋", "番外", "外传", "特典", "SS", "Side Story", "Intermission", "Appendix", "Afterword",
    ]),
]

SCENE_EXCLUSIONS = [
    "单符号行：◇ ◆ * --- ※ ✦ ❖ 等纯符号",
    "空行装饰、场景切换标记",
    "段内时间/地点切换（\"三天后\"/\"另一边\"）",
]


def _format_chapter_types(groups: list[tuple[str, list[str]]]) -> str:
    """把章节类型分组渲染成 markdown 列表。"""
    lines = []
    for name, kws in groups:
        lines.append(f"- **{name}**（必须匹配）：{'、'.join(kws)}")
    return "\n".join(lines)


def _format_scene_exclusions(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


# ============================================================================
# Module 3: Reasoning Steps
# ============================================================================

REASONING_STEPS = """1. **观察样本**：扫描样本，列出所有疑似分节标题行
2. **分类**：区分章节级 vs 场景级（用上述定义判断）
3. **抽象共性**：找出章节级标题的格式规律，而非穷举关键词
   - 例如"~第N败~"应抽象为"装饰符号 + 第N + 单位 + 装饰符号"
   - 例如"第N章 标题"应抽象为"第 + 数字 + 章节单位 + 可选标题"
4. **构造正则**：用字符类 `[...]` 和简短交替 `(...|...|...)` 表达共性
5. **自检**：用样本中实际出现的标题逐一验证正则能否匹配"""


# ============================================================================
# Module 4: Regex Technical Rules
# ============================================================================

REGEX_RULES = [
    "使用 `re.MULTILINE`，`^` 锚定行首",
    "允许 `^\\s*` 前导空白（0-4 个空格/全角空格）",
    "**数字匹配要全面**：`[\\d一二三四五六七八九十百千万零两壹贰叁肆伍陆柒捌玖拾佰仟]+`",
    "**装饰符号匹配要通用**：`[~～◆◇▲■○●△▽★☆]` 而非具体符号",
    "**章节单位用字符类**：`[章回节卷篇部话話败勝幕折]` 而非穷举",
    "**正则末尾用 `.*`** 匹配到行尾（标题常带副标题/正文首句），不要用 `\\s*$`",
    "**不要在正则里硬编码具体书名/渠道名**（如\"BW特典\"应抽象为\"特典\"）",
]


def _format_rules(rules: list[str]) -> str:
    return "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))


# ============================================================================
# Module 5: Few-shot Examples
# ============================================================================

@dataclass(frozen=True)
class FewShotExample:
    """单个 few-shot 示例：样本中观察到的标题 → 期望正则。"""
    observed: list[str]
    regex: str


FEW_SHOT_EXAMPLES: list[FewShotExample] = [
    FewShotExample(
        observed=["第一章 镜湖初遇", "第二章 暗夜密谋", "番外 旧事"],
        regex=r"^\s*(?:第[\d一二三四五六七八九十百千]+[章回节]|番外).*",
    ),
    FewShotExample(
        observed=["~第一话~初遇", "~第二话~密谋", "~尾声~"],
        regex=r"^\s*[~～]?第[\d一二三四五六七八九十百千]+[话話章回败][~～]?.*",
    ),
    FewShotExample(
        observed=["Chapter 1", "Chapter 2", "Epilogue"],
        regex=r"^\s*(?:Chapter\s+\d+|Epilogue).*",
    ),
]


def _format_few_shot(examples: list[FewShotExample]) -> str:
    lines = []
    for ex in examples:
        observed_str = " / ".join(f"`{o}`" for o in ex.observed)
        lines.append(f"输入样本含 {observed_str}")
        lines.append(f"正则：`{ex.regex}`")
        lines.append("")
    return "\n".join(lines).rstrip()


# ============================================================================
# Module 6: Structure Analysis (additional fields beyond chapter regex)
# ============================================================================

STRUCTURE_ANALYSIS = """## 额外结构分析（必填）

除了章节正则，请同时分析以下文档结构特征：

1. **paragraph_style** — 段落分隔方式（枚举）
   - `"blank_line"`: 段落之间用空行分隔（常见于 TXT/MD 原文）
   - `"single_line"`: 段落一行一段，段间无空行（常见于 EPUB 转换后）

2. **scene_separators** — 场景切换标记（字符串数组）
   - 识别**独占一行**的场景分隔符，如 `["*", "●"]`、`["1","2","3"]`、`["──"]`
   - 只识别真正用于场景切换的符号行，**不要**把对话（`「...」`）、省略号（`……`）、短句（`接着──`）算进去
   - 如果没有场景分隔符，返回空数组 `[]`
   - 每个元素必须是 ≤10 字符的短字符串

3. **has_markdown_headings** — 章节标题是否已是 markdown 格式（布尔）
   - `true`: 章节标题已有 `#` 前缀（如 `# 序章`）
   - `false`: 章节标题是裸文本（如 `序章` 独占一行）

4. **confidence** — 本次分析的置信度（0.0-1.0）
   - <0.7 表示样本不足以判断，系统会用保守默认规则"""


# ============================================================================
# Module 7: Output Format
# ============================================================================

OUTPUT_FORMAT = """输出严格 JSON（不要 markdown 代码块）：
{{
  "regex": "正则字符串（单层兼容，等同 levels[0].regex）",
  "levels": [{{"name": "chapter", "regex": "章节正则", "heading_prefix": "#", "optional": false}}],
  "explanation": "简短说明识别到的格式共性",
  "sample_matches": ["样本中实际匹配的示例1", "示例2"],
  "rejected_as_scene": ["被正确排除的场景级标记示例"],
  "paragraph_style": "blank_line 或 single_line（兼容）",
  "paragraph_break": "blank_line | single_line | indented | mixed",
  "scene_separators": ["场景分隔符1", "分隔符2"],
  "has_markdown_headings": false,
  "confidence": 0.92
}}

说明：
- regex: 章节正则（必填，向后兼容）
- levels: 可选，多层级书可提供卷/章/节三层正则，heading_prefix 表示输出用 #/##/###
- 单层书只填 regex 即可，system 会自动转换为 levels"""


# ============================================================================
# Module 8: Input Sample (dynamic)
# ============================================================================

INPUT_SAMPLE_TEMPLATE = """===== 小说开头（前 {head_size} 字）=====
{head}

===== 小说中段（约 50% 位置，{mid_size} 字）=====
{mid}

===== 小说结尾（后 {tail_size} 字）=====
{tail}

===== 全文预扫描统计 =====
{pre_scan}"""


# ============================================================================
# Spec: 可配置项
# ============================================================================

@dataclass
class ChapterPromptSpec:
    """章节检测 prompt 的可配置项。

    覆盖默认值即可适配不同场景（如纯英文小说可移除中文章节类型）。
    """
    # 章节类型分组（None 表示用默认 CHAPTER_TYPE_GROUPS）
    chapter_type_groups: list[tuple[str, list[str]]] | None = None
    # 场景级排除项（None 表示用默认 SCENE_EXCLUSIONS）
    scene_exclusions: list[str] | None = None
    # 正则技术规则（None 表示用默认 REGEX_RULES）
    regex_rules: list[str] | None = None
    # few-shot 示例（None 表示用默认 FEW_SHOT_EXAMPLES）
    few_shot_examples: list[FewShotExample] | None = None
    # 采样大小
    sample_size: int = 5000
    mid_sample_size: int = 2000

    def _chapters(self) -> list[tuple[str, list[str]]]:
        return self.chapter_type_groups if self.chapter_type_groups is not None else CHAPTER_TYPE_GROUPS

    def _scenes(self) -> list[str]:
        return self.scene_exclusions if self.scene_exclusions is not None else SCENE_EXCLUSIONS

    def _rules(self) -> list[str]:
        return self.regex_rules if self.regex_rules is not None else REGEX_RULES

    def _examples(self) -> list[FewShotExample]:
        return self.few_shot_examples if self.few_shot_examples is not None else FEW_SHOT_EXAMPLES


# ============================================================================
# Prompt Builder
# ============================================================================

def build_chapter_prompt(
    head: str,
    tail: str,
    spec: ChapterPromptSpec | None = None,
    mid: str = "",
    pre_scan: str = "",
) -> str:
    """拼接章节检测 prompt。

    Args:
        head: 小说开头样本（已截取）。
        tail: 小说结尾样本（已截取）。
        spec: 可配置项，None 表示用默认值。
        mid: 小说中段样本（约 50% 位置，可选，用于多卷书体例变化检测）。
        pre_scan: 全文 regex 预扫描统计摘要（可选，帮助 LLM 定位结构变化）。

    Returns:
        完整 prompt 字符串。
    """
    spec = spec or ChapterPromptSpec()

    sections: list[str] = []

    # 1. Role & Task
    sections.append(ROLE_TASK)

    # 2. Task Definition（章节级 vs 场景级）
    task_def = [
        "## 任务定义",
        "",
        "识别 chapter-level 分节（要匹配）vs scene-level 分节（不要匹配）：",
        "",
        "**章节级**（必须匹配）——结构性的大块分节：",
        _format_chapter_types(spec._chapters()),
        "",
        "**场景级**（不要匹配）——段内小节分隔：",
        _format_scene_exclusions(spec._scenes()),
    ]
    sections.append("\n".join(task_def))

    # 3. Reasoning Steps
    sections.append("## 推理步骤（请按此思考）\n\n" + REASONING_STEPS)

    # 4. Regex Rules
    sections.append("## 正则技术要求\n\n" + _format_rules(spec._rules()))

    # 5. Few-shot Examples
    sections.append("## few-shot 示例\n\n" + _format_few_shot(spec._examples()))

    # 6. Structure Analysis (additional fields)
    sections.append(STRUCTURE_ANALYSIS)

    # 7. Output Format
    sections.append(OUTPUT_FORMAT)

    # 8. Input Sample (dynamic)
    sample_block = INPUT_SAMPLE_TEMPLATE.format(
        head_size=spec.sample_size,
        mid_size=spec.mid_sample_size,
        tail_size=spec.sample_size,
        head=head,
        mid=mid if mid else "(中段采样未提供)",
        tail=tail,
        pre_scan=pre_scan if pre_scan else "(预扫描统计未提供 — 请仅依据三段样本判断)",
    )
    sections.append(sample_block)

    return "\n\n".join(sections)


# ============================================================================
# 预设 spec（可选）
# ============================================================================

def spec_for_chinese_novel() -> ChapterPromptSpec:
    """纯中文小说预设：移除英文/日文章节类型。"""
    return ChapterPromptSpec(
        chapter_type_groups=[
            ("起始类", ["序", "序章", "序言", "序幕", "楔子", "引子", "前言", "卷首", "开篇"]),
            ("正文章节", ["第N章", "第N回", "第N节", "第N卷", "第N篇", "第N幕"]),
            ("编号型", ["数字序号（一、/1./（一）/第一章）+ 章节标题"]),
            ("结束类", ["尾声", "终章", "结章"]),
            ("附录类", ["后记", "跋", "番外", "外传", "附录"]),
        ],
    )


def spec_for_english_novel() -> ChapterPromptSpec:
    """纯英文小说预设：移除中文/日文章节类型。"""
    return ChapterPromptSpec(
        chapter_type_groups=[
            ("Prologue", ["Prologue", "Preface", "Foreword", "Introduction"]),
            ("Main chapters", ["Chapter N", "Book N", "Part N", "Section N"]),
            ("Ending", ["Epilogue", "Fin", "Conclusion"]),
            ("Appendix", ["Afterword", "Appendix", "Bonus", "Extra", "SS", "Side Story"]),
        ],
        few_shot_examples=[
            FewShotExample(
                observed=["Chapter 1", "Chapter 2", "Epilogue"],
                regex=r"^\s*(?:Chapter\s+\d+|Epilogue).*",
            ),
        ],
    )


def spec_for_japanese_light_novel() -> ChapterPromptSpec:
    """日轻小说预设：强化装饰符号与日文章节单位。"""
    return ChapterPromptSpec(
        chapter_type_groups=[
            ("起始類", ["序", "序章", "序幕", "プロローグ"]),
            ("正文章節", ["第N話", "第N話", "第N敗", "第N勝", "第N巻", "第N章"]),
            ("装飾型", ["被 ~ ～ ◆ ◇ 等符號包裹的章節標記"]),
            ("間章", ["Intermission", "閑話", "幕間"]),
            ("結束類", ["終章", "エピローグ", "尾声"]),
            ("附錄類", ["後記", "あとがき", "番外", "特典", "SS", "Side Story"]),
        ],
    )
