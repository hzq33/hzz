"""Phase 2 verification: test PersonalityProfile extraction on 镜湖风云录."""
import asyncio, sys, os
sys.path.insert(0, "D:/tools/agent")
os.chdir("D:/tools/agent")

from dotenv import load_dotenv
load_dotenv()

async def main():
    # 1. Ingest the test novel
    print("=" * 60)
    print("Step 1: Ingesting 镜湖风云录...")
    print("=" * 60)

    md_path = "tests/test_novel_data/镜湖风云录.md"
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    from src.application.novel.ingest import ingest_novel
    from src.infrastructure.novel_store import NovelVectorStore
    from src.infrastructure.embedding import MockEmbeddingProvider

    store = NovelVectorStore(
        embedding=MockEmbeddingProvider(768),
        backend="faiss",
    )

    result = await ingest_novel(
        file_bytes=content.encode("utf-8"),
        filename="镜湖风云录.md",
        store=store,
        doc_id="镜湖风云录",
    )

    print(f"\nIngest result: {result.success}")
    print(f"  Narrative: {result.narrative_blocks}")
    print(f"  Dialogue: {result.dialogue_blocks}")
    print(f"  QA: {result.qa_blocks}")
    print(f"  Character: {result.character_blocks}")
    print(f"  Characters: {result.characters}")

    # 2. Check character blocks for PersonalityProfile
    print("\n" + "=" * 60)
    print("Step 2: Checking PersonalityProfile in character blocks...")
    print("=" * 60)

    for char_name in result.characters:
        gid = f"镜湖风云录_char_{char_name}"
        block = store.get_block(gid)
        if block is None:
            print(f"\n  {char_name}: NO BLOCK FOUND (gid={gid})")
            continue

        pp = block.personality_profile
        if pp is None:
            print(f"\n  {char_name}: personality_profile = None")
        else:
            print(f"\n  {char_name}:")
            print(f"    traits: {pp.traits}")
            print(f"    speech_patterns: vocabulary={pp.speech_patterns.vocabulary[:60] if pp.speech_patterns else 'None'}...")
            print(f"    catchphrases: {pp.catchphrases}")
            print(f"    emotional_tendencies: {pp.emotional_tendencies[:80]}")

    # 3. Build CharacterCard and print prompt
    if result.characters:
        char_name = result.characters[0]
        print("\n" + "=" * 60)
        print(f"Step 3: Building CharacterCard for {char_name}...")
        print("=" * 60)

        from src.domain.character_card import CharacterCard
        card = await CharacterCard.build(char_name, store, force_rebuild=True)

        print(f"\n  traits: {card.traits}")
        print(f"  speech_patterns: {dict(card.speech_patterns)}")
        print(f"  structured_catchphrases: {card.structured_catchphrases}")
        print(f"\n  --- to_prompt() preview (first 500 chars) ---")
        prompt = card.to_prompt()
        print(prompt[:500])
        print("...")
        print(f"\n  Total prompt length: {len(prompt)} chars")

    print("\nDone!")

asyncio.run(main())
