"""Verify Level 2: LocalLLMCharacterExtractor personality extraction."""
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

TEST_CHARS = [
    {
        "name": "八奈见",
        "snippets": [
            "八奈见咬紧嘴唇，垂下脸。",
            "八奈见抬起头，眼神复杂地看着姬宫。",
            "八奈见站起身，整理了一下裙摆。",
            "八奈见脚步一顿，回过头来。",
        ],
        "dialogues": [
            "别说了，没关系。",
            "没什么，只是在聊天而已。姬宫你呢？",
            "嗯，走吧。",
            "我也一起去。",
        ],
    },
    {
        "name": "温水",
        "snippets": [
            "温水看着她的侧脸，叹了口气。",
            "温水皱起眉头，转身看向窗外。",
        ],
        "dialogues": [
            "我没有在说那个。你误会了。",
            "我们该走了。再不走校门要关了。",
        ],
    },
    {
        "name": "姬宫",
        "snippets": [
            "姬宫踩着轻快的步伐走了过来，笑容灿烂得让人睁不开眼。",
            "姬宫歪了歪头，眨了眨眼睛。",
            "姬宫突然拉住温水的袖子。",
        ],
        "dialogues": [
            "你们两个在做什么呢？放学了还不回家。",
            "我来拿忘带的课本呀。不过看到你们两个，感觉更有趣呢。",
            "温水同学，明天周末，要不要一起去看电影？",
        ],
    },
]

async def main():
    from src.domain.novel.character_local_llm import LocalLLMCharacterExtractor

    extractor = LocalLLMCharacterExtractor()
    print("Extracting personality for 3 characters via Qwen-1.8B subprocess...\n")

    for char in TEST_CHARS:
        profile = await extractor.extract_personality(
            char["name"], char["snippets"], char["dialogues"]
        )
        print(f"{char['name']}:")
        print(f"  personality:    {profile.get('personality', 'N/A')}")
        print(f"  speaking_style: {profile.get('speaking_style', 'N/A')}")
        print(f"  is_character:   {profile.get('is_character', 'N/A')}")
        print(f"  role_hint:      {profile.get('role_hint', 'N/A')}")
        print()

asyncio.run(main())
