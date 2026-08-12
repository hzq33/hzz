# -*- coding: utf-8 -*-
"""角色图谱功能验证：build → save → load → 检索上下文消费。

用法：
    PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/dev/ab_harvest_vs_cluener/verify_graph.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DOC = "败犬女主太多了__vol08"


def load_blocks():
    """从 LanceDB 拉取 vol01 的 NovelBlock（narrative/dialogue/character 三类）。"""
    from src.infrastructure.lance_backend import LanceDBBackend

    backend = LanceDBBackend(db_path="./data/novel_lance")
    arrow = backend._table.to_arrow()
    mask = [str(d) == DOC for d in arrow.column("doc_id").to_pylist()]
    sub = arrow.filter(mask)
    rows = sub.to_pylist()
    blocks = [backend._row_to_block(r) for r in rows]
    narrative = [b for b in blocks if b.block_type == "narrative"]
    dialogue = [b for b in blocks if b.block_type == "dialogue"]
    character = [b for b in blocks if b.block_type == "character"]
    print(f"blocks: narrative={len(narrative)} dialogue={len(dialogue)} character={len(character)}")
    return narrative, dialogue, character


def load_alias_map():
    """模拟 blocks._load_series_alias_map：败犬系列 alias.json → canonical→aliases。"""
    from src.api.routers.alias_roster import read_alias

    data = read_alias("败犬女主太多了")
    alias_map = {}
    for e in data.get("entities") or []:
        canon = str(e.get("canonical_name") or "").strip()
        if canon:
            alias_map[canon] = [
                str(a).strip() for a in (e.get("aliases") or []) if str(a).strip()
            ]
    return alias_map or None


def main():
    from src.infrastructure.character_graph import CharacterGraph

    narrative, dialogue, character = load_blocks()
    alias_map = load_alias_map()
    print(f"alias_map: {len(alias_map) if alias_map else 0} 个 canonical")

    # ── 1. 构建图谱 ──
    graph = CharacterGraph().build(
        character_blocks=character,
        dialogue_blocks=dialogue,
        narrative_blocks=narrative,
        alias_map=alias_map,
        noise_names={"主角", "旁白", "史莱姆", "哥布林"},
    )
    n_nodes = graph.graph.number_of_nodes()
    n_edges = graph.graph.number_of_edges()
    print(f"\n[构建] 节点={n_nodes} 边={n_edges}")
    assert n_nodes > 0, "图谱节点为 0！"
    print(f"节点样例: {sorted(graph.graph.nodes())[:12]}")

    # ── 2. 保存 + 重新加载 ──
    tmp = ROOT / "data" / "graphs" / f"{DOC}.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    graph.save(str(tmp))
    loaded = CharacterGraph.load(str(tmp))
    print(f"[持久化] save→load 节点一致: {loaded.graph.number_of_nodes() == n_nodes}")
    tmp.unlink(missing_ok=True)

    # ── 3. 关系查询 ──
    if n_nodes:
        sample = sorted(graph.graph.nodes())[0]
        rels = graph.get_relations(sample)
        print(f"\n[关系查询] '{sample}' 的关系数: {len(rels)}")
        for r in rels[:5]:
            print(f"   → {r.character} 类型={r.edge_types} 互动={r.interaction_count} 章节={r.chapters[:3]}")

    # ── 4. 检索上下文消费（模拟 to_context_string）──
    if n_nodes:
        ctx = graph.to_context_string([], limit=3)
        print(f"\n[检索上下文] to_context_string 输出 {len(ctx)} 字符")
        print(ctx[:300].replace("\n", " | "))


if __name__ == "__main__":
    main()
