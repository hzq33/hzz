"""Smoke-test CharacterGraph build+save with synthetic blocks (offline).

Diagnoses why data/graphs/ never produced files (worklog follow-up item 3).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.domain.novel.models import NovelBlock


def main() -> None:
    from src.infrastructure.character_graph import CharacterGraph

    char_blocks = [
        NovelBlock(
            global_id="c1", doc_id="t", block_type="character",
            character_name=n, personality="x", vec_text_character=n,
        )
        for n in ("利姆露", "维鲁多拉", "朱菜", "哥布莉娜")
    ]
    dia_blocks = [
        NovelBlock(
            global_id="d1", doc_id="t", block_type="dialogue",
            characters=["利姆露", "维鲁多拉"], dialogues=[], vec_text_dialogue="d",
            all_person=["利姆露", "维鲁多拉"],
        ),
        NovelBlock(
            global_id="d2", doc_id="t", block_type="dialogue",
            characters=["利姆露", "朱菜"], dialogues=[], vec_text_dialogue="d",
            all_person=["利姆露", "朱菜"],
        ),
    ]
    g = CharacterGraph().build(char_blocks, dia_blocks, [])
    print("nodes:", g.graph.number_of_nodes(), "edges:", g.graph.number_of_edges())
    print("degrees:", dict(g.graph.degree()))
    save_path = ROOT / "data/graphs/_smoke_test.json"
    g.save(str(save_path))
    print("saved:", save_path.exists(), save_path.stat().st_size if save_path.exists() else 0)
    loaded = CharacterGraph.load(str(save_path))
    print("loaded nodes:", loaded.graph.number_of_nodes())
    save_path.unlink(missing_ok=True)
    print("cleaned up")


if __name__ == "__main__":
    main()
