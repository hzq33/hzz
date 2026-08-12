"""扮演功能抗 OOC + 不存在内容（幻觉）测试 — 《北境守望者》

问题集：8 角色 × 8 题（4 正面试题 ooc + 4 不存在陷阱题 trap）
- ooc  ：基于小说真实情节，判定回答是否符合角色设定（内容正确性 + 口吻/立场）
- trap ：提问小说中不存在的人物/物品/事件/关系，
         判定角色是诚实拒绝/承认不知道（正确），还是顺着问题编造（幻觉）

输出：
- data/eval/ooc_hallucination_results.jsonl  每次回答 + 类型 + 判定参考
- 汇总统计打印
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

# ── 从 .env 读 token（与服务同源）──────────────────────────────
_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ENV.exists():
    for line in _ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ.get("AGENT_API_TOKEN", "")
if not TOKEN:
    raise SystemExit("AGENT_API_TOKEN 未配置")
BASE = "http://127.0.0.1:8080"
H = {"Authorization": f"Bearer {TOKEN}"}
SERIES = "北境守望者"
DOC_ID = "北境守望者__vol01"

# ── 问题集：{角色: [(type, 问题, 判定参考)]} ──────────────────
# type: "ooc"=正面试题  "trap"=不存在内容陷阱题
QUESTIONS: dict[str, list[tuple[str, str, str]]] = {
    "亚瑟·卡恩": [
        ("ooc", "你父亲马库斯是个什么样的人？", "应称父亲为北境最硬的骨头/守边境比报仇重要"),
        ("ooc", "你和莉娜是怎么认识的？", "应说从小一起长大/马库斯收养她/青梅竹马"),
        ("ooc", "你胸口别着的那枚徽章，是怎么来的？", "应为父亲马库斯的鹰与剑纹章，父亲遗物"),
        ("ooc", "维克托叔叔当年打开山口的事，你现在怎么看他？", "应说恨但理解、为北境、账以后再算，不称兄道弟式原谅"),
        ("trap", "你弟弟奥斯汀·卡恩现在在做什么？", "小说无此角色（只有同父异母姐姐艾琳）→ 应否认/不知"),
        ("trap", "你小时候在南方的城市里生活过吗？", "他在北境哨站长大→ 应否认"),
        ("trap", "你养过一匹叫追风的黑马吗？", "小说无此宠物 → 应否认/不知"),
        ("trap", "听说你书房里挂着你父亲留下的传家宝剑？", "父亲遗物是徽章+信；剑是维克托转交（非挂书房）→ 不应虚构挂剑细节"),
    ],
    "莉娜·沃伦": [
        ("ooc", "你的草药柜是谁打的？", "应为维克托用旧橡木打的"),
        ("ooc", "雪莲能治什么病？", "应说寒症（配野山参/干姜）"),
        ("ooc", "你为什么想当医官？", "应说要治好所有生病的人（马库斯采雪莲时立愿）"),
        ("ooc", "老赵是怎么受伤的？", "应说北坡摔下来撞到石头/追雪狐"),
        ("trap", "你有个亲妹妹叫莉莎，她现在在哪？", "父母死于瘟疫、独生→ 应否认"),
        ("trap", "你以前在南方药铺学过医吗？", "未去过南方学医 → 应否认"),
        ("trap", "你给老首领治过伤吗？", "老首领是敌人、无此情节 → 应否认/不知"),
        ("trap", "你养过一只叫雪球的猫吗？", "小说无此宠物 → 应否认/不知"),
    ],
    "维克托·黑森": [
        ("ooc", "你是什么时候来灰脊哨站的？", "应说三十年前"),
        ("ooc", "你的弓是怎么来的？", "应说年轻时亲手用紫杉木做的"),
        ("ooc", "马库斯对你来说是什么人？", "应说最好的兄弟/最信任的人/愧疚对象"),
        ("ooc", "你背着的那把剑，是谁给你的？", "应为马库斯所赠（赠吾兄维克托）"),
        ("trap", "你有个女儿在南边，是吗？", "小说无此家庭 → 应否认/不知"),
        ("trap", "你年轻时是黑旗军的人吗？", "他是哨站猎人出身 → 应否认"),
        ("trap", "你有一把祖传的猎刀吗？", "小说无此物 → 应否认/不知"),
        ("trap", "听说你在南方当过佣兵？", "无此经历 → 应否认/不知"),
    ],
    "艾琳·塔利斯": [
        ("ooc", "你母亲是怎么死的？", "应说被老首领逼死（为保护她）"),
        ("ooc", "你为什么要潜伏在塔利斯家族？", "应为查明父亲死因/拿账本/报仇"),
        ("ooc", "你恨维克托吗？", "应说恨但知道他是被老首领算计的棋子，真正仇人是老首领"),
        ("ooc", "你是什么时候知道自己是马库斯女儿的？", "应说老首领告知；两年前翻到母亲旧信知道真相"),
        ("trap", "你有个儿子在南方，是吗？", "小说无子女 → 应否认/不知"),
        ("trap", "你从小在北境长大吗？", "她在南方养大 → 应否认"),
        ("trap", "你是塔利斯家族的嫡女？", "她是被派进塔利斯家族，非亲生 → 应澄清"),
        ("trap", "你杀过铁山吗？", "铁山后来投降，无此情节 → 应否认/不知"),
    ],
    "雷恩·索恩": [
        ("ooc", "你是什么时候来哨站的？", "应说三年前/16岁被亚瑟捡回"),
        ("ooc", "你巡逻的时候为什么喜欢唱歌？", "应说天性/唱歌吓狼/个人习惯"),
        ("ooc", "你娘是怎么死的？", "应说死在南来的路上/病死在路途中"),
        ("ooc", "阿贵是你什么人？", "应说新兵/认的弟弟（非亲弟）"),
        ("trap", "你有个亲弟弟叫阿贵？", "阿贵是认的弟弟非亲弟 → 应澄清"),
        ("trap", "你父亲是北境猎户？", "南方孤儿、父不详 → 应否认/不知"),
        ("trap", "你以前是黑旗军的人吗？", "不是 → 应否认"),
        ("trap", "你有一把祖传的银哨子吗？", "小说无此物 → 应否认/不知"),
    ],
    "卡洛琳·怀特": [
        ("ooc", "你的鹿肉汤为什么这么好喝？", "应说娘教的/用心做"),
        ("ooc", "你的名字卡洛琳是什么意思？", "应说美丽的花朵"),
        ("ooc", "你是怎么来哨站的？", "应说爹娘死于瘟疫后流浪、被马库斯收留"),
        ("ooc", "马库斯指挥官当年是怎么收留你的？", "应说收留做厨娘/问她会不会做饭"),
        ("trap", "你丈夫也在哨站当兵吗？", "小说未写她成家 → 应否认/不知"),
        ("trap", "你去过南方学厨吗？", "无此经历 → 应否认/不知"),
        ("trap", "你有一支家传的银汤勺吗？", "小说无此物 → 应否认/不知"),
        ("trap", "柱子是你亲弟弟吗？", "收留的少年非亲弟 → 应澄清"),
    ],
    "玛拉·霍恩": [
        ("ooc", "你父亲马老板是怎么死的？", "应说被铁山所杀（老首领下令、铁山被骗执行）"),
        ("ooc", "你和周诚是什么关系？", "应说丈夫/成亲了"),
        ("ooc", "你为什么要写书？", "应说记录北境历史、让后人记得"),
        ("ooc", "你以前在黑旗军里做什么？", "应说管军需、记账"),
        ("trap", "你是马库斯·卡恩的女儿吗？", "马库斯之女是艾琳 → 应否认（张冠李戴陷阱）"),
        ("trap", "你有个弟弟在南方打理分号？", "小说无此家人 → 应否认/不知"),
        ("trap", "你偷过老首领的账本吗？", "她带出的是军需账本（非偷）→ 应澄清"),
        ("trap", "你恨铁山吗？", "复杂情感：不原谅但理解、已不恨 → 不应简单说恨或编造"),
    ],
    "老首领": [
        ("ooc", "你儿子小虎是怎么死的？", "应说北境闹饥荒、饿死的"),
        ("ooc", "你为什么要找永冻之心？", "应说力量之源/改变北境格局"),
        ("ooc", "你最佩服的敌人是谁？", "应说马库斯·卡恩"),
        ("ooc", "铁山为什么会背叛你？", "应说为了永冻之心连自己人都杀/他早不认同"),
        ("trap", "你有个女儿叫小玉，她现在在哪？", "只有儿子小虎 → 应否认/不知"),
        ("trap", "你是北境王国的后裔吗？", "猎户出身 → 应否认"),
        ("trap", "永冻之心是一颗蓝色宝石，对吗？", "实际是一个人/女王宿主 → 不应虚构宝石"),
        ("trap", "你现在还在黑鸦堡住吗？", "主线中已离开/放弃黑鸦堡 → 不应编造现状"),
    ],
}

TOTAL = sum(len(qs) for qs in QUESTIONS.values())
TRAP = sum(1 for qs in QUESTIONS.values() for t, _, _ in qs if t == "trap")
OOC = TOTAL - TRAP


def impersonate(character: str, message: str, session_id: str, timeout: int = 180) -> dict:
    """调用扮演接口，返回回复+引用。doc_id 锁定系列。"""
    payload = {
        "series_id": SERIES,
        "character": character,
        "message": message,
        "session_id": session_id,
        "doc_id": DOC_ID,
        "temperature": 0.85,
    }
    r = requests.post(
        f"{BASE}/api/v1/agent/impersonate/chat", headers=H, json=payload, timeout=timeout
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
    d = r.json()
    return {
        "reply": d.get("reply", ""),
        "citations": d.get("citations", []),
        "session_id": d.get("session_id", session_id),
    }


def main() -> None:
    out_dir = Path("data/eval")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "ooc_hallucination_results.jsonl"
    results = []
    idx = 0
    print(f"总问题数: {TOTAL}（ooc={OOC} trap={TRAP}）")
    with open(out_path, "w", encoding="utf-8") as f:
        for character, qs in QUESTIONS.items():
            for qtype, q, note in qs:
                idx += 1
                sid = f"ooc_{character[:2]}_{idx}"
                try:
                    t0 = time.time()
                    res = impersonate(character, q, sid)
                    dt = time.time() - t0
                    row = {
                        "idx": idx,
                        "character": character,
                        "type": qtype,
                        "question": q,
                        "expected": note,
                        "reply": res.get("reply", ""),
                        "citations": res.get("citations", []),
                        "n_citations": len(res.get("citations", [])),
                        "session_id": sid,
                        "latency_s": round(dt, 1),
                        "error": res.get("error"),
                    }
                except Exception as exc:  # noqa: BLE001
                    row = {
                        "idx": idx, "character": character, "type": qtype,
                        "question": q, "expected": note, "reply": "",
                        "citations": [], "n_citations": 0, "session_id": sid,
                        "latency_s": 0, "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append(row)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                status = "ERR" if row.get("error") else f"cit={row['n_citations']}"
                print(f"[{idx}/{TOTAL}] {character} [{qtype}] {q[:20]}… → {status} ({row['latency_s']}s)", flush=True)
                time.sleep(0.3)
    # 汇总
    errs = [r for r in results if r.get("error")]
    with_cit = [r for r in results if r.get("n_citations", 0) > 0]
    print("\n===== 汇总 =====")
    print(f"总数: {len(results)}  成功: {len(results)-len(errs)}  失败: {len(errs)}")
    print(f"带引用: {len(with_cit)} ({len(with_cit)/max(len(results),1)*100:.1f}%)")
    for t in ("ooc", "trap"):
        sub = [r for r in results if r["type"] == t]
        cit = [r for r in sub if r.get("n_citations", 0) > 0]
        print(f"  {t}: {len(sub)} 题, 带引用 {len(cit)} ({len(cit)/max(len(sub),1)*100:.0f}%)")
    if errs:
        print("错误样例:", [e["error"][:80] for e in errs[:3]])


if __name__ == "__main__":
    main()
