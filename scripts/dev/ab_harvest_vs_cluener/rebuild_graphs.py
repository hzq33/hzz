# -*- coding: utf-8 -*-
"""批量重建所有卷的角色关系图谱 → data/graphs/<doc_id>.json。

用法：
    PYTHONIOENCODING=utf-8 ./venv/Scripts/python.exe scripts/dev/ab_harvest_vs_cluener/rebuild_graphs.py [--doc 败犬女主太多了__vol08]

修复后重建：alias 归一（短名→canonical）+ 噪声过滤，避免节点分裂。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", default=None, help="只重建指定 doc_id")
    args = parser.parse_args()

    from src.application.novel.ingest.blocks import _load_series_alias_map
    from src.infrastructure.character_graph import CharacterGraph
    from src.infrastructure.lance_backend import LanceDBBackend

    backend = LanceDBBackend(db_path="./data/novel_lance")
    arrow = backend._table.to_arrow()
    docs = arrow.column("doc_id").to_pylist()
    types = arrow.column("block_type").to_pylist()
    rows = arrow.to_pylist()

    per_doc: dict[str, list[dict]] = {}
    for d, t, r in zip(docs, types, rows):
        if t in ("narrative", "dialogue", "character"):
            per_doc.setdefault(d, []).append(r)

    graph_dir = ROOT / "data" / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for doc_id, doc_rows in sorted(per_doc.items()):
        if args.doc and args.doc != doc_id:
            continue
        blocks = [backend._row_to_block(r) for r in doc_rows]
        narrative = [b for b in blocks if b.block_type == "narrative"]
        dialogue = [b for b in blocks if b.block_type == "dialogue"]
        character = [b for b in blocks if b.block_type == "character"]
        if not (character and dialogue):
            summary.append((doc_id, 0, 0, "跳过(无角色块/对话块)"))
            continue
        alias_map = _load_series_alias_map(doc_id)
        # P2: 传入事实源关系（story_analysis 快照），图谱关系边从此构建
        relations = None
        try:
            from src.domain.novel.story_analysis.config import load_analysis
            from src.domain.novel.query_parse import series_id_from_doc_id

            snap = load_analysis(series_id_from_doc_id(doc_id))
            relations = list(snap.relations or []) if snap else None
        except Exception:
            relations = None
        try:
            graph = CharacterGraph().build(
                character_blocks=character,
                dialogue_blocks=dialogue,
                narrative_blocks=narrative,
                alias_map=alias_map,
                noise_names={"主角", "旁白", "史莱姆", "哥布林"},
                relations=relations,
            )
            n_nodes = graph.graph.number_of_nodes()
            n_edges = graph.graph.number_of_edges()
            if n_nodes == 0:
                summary.append((doc_id, 0, 0, "跳过(0 节点)"))
                continue
            path = graph_dir / f"{doc_id}.json"
            graph.save(str(path))
            summary.append((doc_id, n_nodes, n_edges, "OK"))
        except Exception as e:  # noqa: BLE001
            summary.append((doc_id, 0, 0, f"失败: {e}"))

    print(f"\n{'doc_id':44} 节点  边  状态")
    ok = 0
    for doc_id, n, e, status in summary:
        print(f"  {doc_id[:42]:42} {n:4} {e:4}  {status}")
        if status == "OK":
            ok += 1
    print(f"\n成功 {ok}/{len(summary)} 卷 → {graph_dir}")


if __name__ == "__main__":
    main()
