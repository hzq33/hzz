# -*- coding: utf-8 -*-
"""从已落盘 inventory 数据确定性清洗角色名单（不重跑 LLM）。

规则：
  - 删除：泛称/身份词、种族/物种、地名、普通物品、注释人物、作者（可配置 DELETE_GENERIC）
  - 保留：会说话的技能/系统音（KEEP_SPEAKING_SKILLS，如史莱姆的大贤者/捕食者）
  - 其余（真实人物）保留
清洗后重算 importance（mention TopN），写回原文件（自动备份 .bak）。

用法：python scripts/dev/clean_inventory.py <series_id>
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))

from src.domain.novel.character_policy import ROLE_WORDS

# ── 删除：泛称/种族/地名/物品/注释人物/作者（可维护）──
DELETE_GENERIC = ROLE_WORDS | {
    # 泛称/称号
    "勇者", "魔王", "技能", "国家", "城镇", "蘑菇", "回复药", "长老", "大姐",
    "黑骑士", "爆焰支配者", "咒术师", "异变者", "狗头人", "狗头人商人", "商人",
    "狗头人族商人", "国王", "士兵", "冒险者互助会", "城镇",
    # 种族/物种
    "矮人", "人类", "精灵", "魔人", "史莱姆", "牙狼族", "岚牙狼族", "滚刀哥布林",
    "长耳族", "黑狼", "黑蛇", "三头蛇", "有翼狮", "孤刃虎", "伊弗利特", "火属性高阶精灵",
    # 地名
    "矮人王国", "朱拉大森林", "东方帝国", "东方平原", "英格拉西亚", "城墙周边居住区",
    "朱拉森林",
    # 物品/普通名词
    "火焰短剑", "抗魔面具", "火瘴茸", "斗篷", "魔法武具", "发焰筒", "回复药",
    # 作者/插画/注释/其他作品
    "みっつばー", "伏濑", "木原部长", "龟山", "田村", "绪果", "赫丘勒",
    "克莉丝蒂", "海伦・凯勒", "泽渡", "泽渡美穗",
    # 技能相关但非说话技能（不确定归这里，KEEP 优先）
    "钢丝", "水刀", "思念网", "吸血", "毒喷雾", "威压", "热源感应", "超音波",
    "超嗅觉", "麻痹喷雾", "魔力感知", "震声炮", "焰流", "爆裂魔法", "回复魔法",
    "魔法筒",
}

# ── 保留：会说话的技能/系统音（KEEP 优先于 DELETE）──
KEEP_SPEAKING_SKILLS = {
    "大贤者", "捕食者", "魔法筒", "焰之巨人", "钢丝", "水刀", "思念网",
    "吸血", "毒喷雾", "威压", "热源感应", "超音波", "超嗅觉", "麻痹喷雾",
    "魔力感知", "震声炮", "焰流", "爆裂魔法", "回复魔法",
}


def clean_inventory(series_id: str) -> dict:
    inv_path = ROOT / "data" / "inventories" / f"{series_id}.json"
    if not inv_path.exists():
        raise SystemExit(f"not found: {inv_path}")
    data = json.loads(inv_path.read_text(encoding="utf-8"))
    candidates = data.get("candidates") or []

    removed: list[dict] = []
    kept: list[dict] = []
    for c in candidates:
        name = str(c.get("name") or "").strip()
        if name in KEEP_SPEAKING_SKILLS:
            kept.append(c)
        elif name in DELETE_GENERIC:
            removed.append(c)
        else:
            kept.append(c)

    # 重算 importance（mention TopN：main 5 / supporting 20 / 其余 extra）
    ranked = sorted(kept, key=lambda x: -(x.get("mention_count") or 0))
    for i, c in enumerate(ranked):
        c["importance"] = "main" if i < 5 else ("supporting" if i < 25 else "extra")

    # 备份 + 写回
    bak = inv_path.with_suffix(".json.bak")
    shutil.copy2(inv_path, bak)
    data["candidates"] = ranked
    data["cleaned"] = {
        "removed": [{"name": c.get("name"), "mention": c.get("mention_count")} for c in removed],
        "keep_speaking_skills": sorted(KEEP_SPEAKING_SKILLS & {c.get("name") for c in ranked}),
    }
    inv_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"kept": ranked, "removed": removed, "backup": str(bak)}


if __name__ == "__main__":
    series = sys.argv[1] if len(sys.argv) > 1 else "关于我转生变成史莱姆这档事"
    result = clean_inventory(series)
    print(f"清洗完成: 保留 {len(result['kept'])} / 删除 {len(result['removed'])} | 备份: {result['backup']}")
    print("\n删除的项:")
    for r in result["removed"]:
        print(f"  {r.get("name")} (mention={r.get("mention_count") or r.get("mention")})")
    print("\n=== 清洗后 main/supporting ===")
    for c in sorted(result["kept"], key=lambda x: -x.get("mention_count", 0)):
        if c.get("importance") in ("main", "supporting"):
            print(f"  {c.get('name',''):12s} {c.get('importance'):10s} {c.get('mention_count',0)}")
