"""Re-run character inventory LLM normalization for 败犬女主太多了."""
import asyncio, os, sys
from pathlib import Path

ROOT = Path(r"D:\tools\agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "venv" / "Lib" / "site-packages"))

from dotenv import load_dotenv
load_dotenv(encoding="utf-8-sig")

from src.shared.llm import SharedLLMClient
from src.domain.novel.character_inventory import (
    build_character_inventory,
    inventory_config,
    persist_inventory_candidates,
    seed_names_from_inventory,
)

SERIES = "败犬女主太多了"
DOC_ID = "败犬女主太多了__vol01"

async def main():
    # Load document text from LanceDB
    import lancedb
    db = lancedb.connect("data/novel_lance")
    t = db.open_table("novel_blocks")
    df = t.to_pandas()
    mask = (df["doc_id"] == DOC_ID) & (df["block_type"] == "narrative")
    texts = df[mask]["narrative_text"].dropna().tolist()
    full_text = "\n".join(str(x) for x in texts)
    print(f"Document text: {len(full_text)} chars, {len(texts)} blocks")

    # Build LLM client
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    llm = SharedLLMClient(
        primary={
            "model": "deepseek-v4-flash",
            "api_key": api_key,
            "base_url": "https://api.deepseek.com",
        },
        temperature=0.0,
        max_tokens=4096,
    )

    # Fake document with chapters
    class FakeDoc:
        pass
    doc = FakeDoc()
    doc.text = full_text
    # Try to parse chapters from structured data
    chapters_group = df[mask].groupby("chapter_title")
    doc.chapters = []
    for title, group in chapters_group:
        ch = type("Chapter", (), {})()
        ch.title = title
        ch.text = "\n".join(str(x) for x in group["narrative_text"].dropna().tolist())
        doc.chapters.append(ch)
    print(f"Chapters: {len(doc.chapters)}")

    # Run inventory
    cfg = inventory_config()
    print(f"Config: enabled={cfg.get('enabled')}, sync={cfg.get('sync_on_ingest')}")
    print("Running LLM normalization...")
    
    result = await build_character_inventory(
        doc,
        series_id=SERIES,
        llm_client=llm,
        config=cfg,
    )
    
    print(f"\nResult:")
    print(f"  characters: {len(result.characters)}")
    print(f"  dropped: {len(result.dropped)}")
    print(f"  llm_calls: {result.llm_calls}")
    print(f"  llm_skipped: {result.llm_skipped}")
    print(f"  meta: {result.meta}")
    
    if result.characters:
        print(f"\nTop 20 characters:")
        for c in result.characters[:20]:
            print(f"  {c.canonical_name:14s} mention={c.mention_count:4d} aliases={c.aliases[:3]}")
        
        # Persist
        persist_inventory_candidates(
            series_id=SERIES,
            doc_id=DOC_ID,
            inventory=result,
        )
        seed = seed_names_from_inventory(result)
        print(f"\nSeed names: {seed}")
        
        # Now rebuild roster from result
        from src.domain.novel.character_inventory.roster import persist_inventory_roster
        import lancedb as lb
        db2 = lb.connect("data/novel_lance")
        t2 = db2.open_table("novel_blocks")
        df2 = t2.to_pandas()
        dlg_mask2 = df2["block_type"] == "dialogue"
        dlg_df2 = df2[dlg_mask2]
        
        # Build dialogue blocks list
        dialogue_blocks = []
        import json
        from src.domain.novel.models import DialogueTurn, NovelBlock
        for _, row in dlg_df2.iterrows():
            try:
                dialogues_json = row.get("dialogues_json", "[]")
                if isinstance(dialogues_json, str):
                    turns_data = json.loads(dialogues_json)
                else:
                    turns_data = dialogues_json or []
                turns = []
                for td in turns_data:
                    if isinstance(td, dict):
                        turns.append(DialogueTurn(
                            turn=td.get("turn", 0),
                            speaker=str(td.get("speaker", "未知")),
                            content=str(td.get("content", "")),
                            confidence=float(td.get("confidence", 0)),
                        ))
                if turns:
                    block = NovelBlock(
                        global_id=row.get("global_id", ""),
                        doc_id=row.get("doc_id", ""),
                        chapter_title=row.get("chapter_title", ""),
                        block_type="dialogue",
                        dialogues=turns,
                    )
                    dialogue_blocks.append(block)
            except Exception:
                continue
        
        roster = persist_inventory_roster(
            series_id=SERIES,
            doc_id=DOC_ID,
            inventory=result,
            dialogue_blocks=dialogue_blocks,
        )
        print(f"\nRoster rebuilt: {len(roster.characters)} characters")
        for e in roster.characters[:10]:
            print(f"  {e.name:14s} dlg={e.dialogue_count:4d}")
    
    await llm.close()
    print("\nDone!")

asyncio.run(main())
