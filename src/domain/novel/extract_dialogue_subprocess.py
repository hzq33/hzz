"""Standalone dialogue extraction subprocess — runs in venv_qwen.

Reads JSON input: {"text": "...", "model_path": "...", "mode": "single"|"official"}
Writes JSON output: {"turns": [...], "error": null, "chunks": N, ...}

Modes:
  single   — legacy: one-shot on (truncated) text
  official — Chat-Haruhi notebook alignment:
             TOKEN_PER_CHUNK≈600, multi-chunk, previous summary prepended

Usage (from main venv):
    venv_qwen/Scripts/python src/domain/novel/extract_dialogue_subprocess.py input.json
"""

from __future__ import annotations

import json
import re
import sys
import time

# ── Haruhi official system prompt (must match HF model card) ──
SYSTEM_PROMPT = (
    "给定input paragraph，抽取其中的对话，并输出为json格式 "
    "Let's think it step by step "
    "1. summarize input paragraph into bullet format，存储在summary字段 "
    "2. 抽取每一句对话的内容 dialogue，判断每一句话的说话人 said by, "
    "存储在conversations中"
)

# Official notebook default (Dialogue_Speaker_Extract_Test.ipynb)
TOKEN_PER_CHUNK = 600
SINGLE_MAX_CHARS = 3000

_MODEL = None
_TOKENIZER = None


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        if input_path:
            with open(input_path, encoding="utf-8") as f:
                req = json.load(f)
        else:
            req = json.loads(sys.stdin.read())
        text = req["text"]
        model_path = req.get("model_path", "models/Haruhi-Dialogue-Speaker-Extract_qwen18")
        mode = req.get("mode", "official")
        token_per_chunk = int(req.get("token_per_chunk", TOKEN_PER_CHUNK))
    except Exception as e:
        json.dump({"turns": [], "error": f"Invalid input: {e}"}, sys.stdout)
        sys.exit(1)

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        t0 = time.perf_counter()

        global _MODEL, _TOKENIZER
        if _MODEL is None:
            _TOKENIZER = AutoTokenizer.from_pretrained(
                model_path, trust_remote_code=True
            )
            bnb = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
            _MODEL = AutoModelForCausalLM.from_pretrained(
                model_path,
                trust_remote_code=True,
                quantization_config=bnb,
                device_map="auto",
            )
            _MODEL.eval()

        model = _MODEL
        tokenizer = _TOKENIZER
        load_time = time.perf_counter() - t0

        if mode == "single":
            result = _run_single(model, tokenizer, text, t0, load_time)
        else:
            result = _run_official(
                model, tokenizer, text, t0, load_time, token_per_chunk
            )

        json.dump(result, sys.stdout, ensure_ascii=False)
        sys.exit(0)

    except Exception as e:
        json.dump({"turns": [], "error": str(e)}, sys.stdout)
        sys.exit(1)


def _run_single(model, tokenizer, text, t0, load_time):
    if len(text) > SINGLE_MAX_CHARS:
        text = text[:SINGLE_MAX_CHARS] + "..."
    resp, _ = model.chat(tokenizer, text, history=[], system=SYSTEM_PROMPT)
    gen_time = time.perf_counter() - t0 - load_time
    summary, turns = _parse_full(resp)
    return {
        "turns": [{"speaker": s, "content": c} for s, c in turns],
        "summary": summary,
        "error": None,
        "mode": "single",
        "chunks": 1,
        "load_s": round(load_time, 2),
        "gen_s": round(gen_time, 2),
    }


def _run_official(model, tokenizer, text, t0, load_time, token_per_chunk):
    """Align with Chat-Haruhi notebook: 600-token chunks + summary chaining."""
    chunks = _chunk_by_tokens(text, tokenizer, token_per_chunk)
    all_turns: list[tuple[str, str]] = []
    summaries: list[str] = []
    chunk_meta: list[dict] = []
    prev_summary = ""

    for i, chunk in enumerate(chunks):
        if prev_summary:
            inp = prev_summary + "\n" + chunk
        else:
            inp = chunk

        c0 = time.perf_counter()
        resp, _ = model.chat(tokenizer, inp, history=[], system=SYSTEM_PROMPT)
        elapsed = time.perf_counter() - c0

        summary, turns = _parse_full(resp)
        if not summary and not turns:
            # JSON parse failed — keep going without poisoning next chunk
            chunk_meta.append({
                "i": i,
                "chars": len(chunk),
                "turns": 0,
                "gen_s": round(elapsed, 2),
                "parse_ok": False,
                "raw_preview": (resp or "")[:120],
            })
            continue

        prev_summary = summary or prev_summary
        if summary:
            summaries.append(summary)
        all_turns.extend(turns)
        chunk_meta.append({
            "i": i,
            "chars": len(chunk),
            "turns": len(turns),
            "gen_s": round(elapsed, 2),
            "parse_ok": True,
            "speakers": sorted({s for s, _ in turns if s and s != "未知"}),
        })

    gen_time = time.perf_counter() - t0 - load_time
    return {
        "turns": [{"speaker": s, "content": c} for s, c in all_turns],
        "summaries": summaries,
        "error": None,
        "mode": "official",
        "chunks": len(chunks),
        "chunk_meta": chunk_meta,
        "load_s": round(load_time, 2),
        "gen_s": round(gen_time, 2),
    }


def _chunk_by_tokens(text: str, tokenizer, token_per_chunk: int) -> list[str]:
    """Line-aware packing to ~token_per_chunk (official notebook style)."""
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    current_len = 0

    for line in lines:
        try:
            line_len = len(tokenizer.encode(line, add_special_tokens=False))
        except Exception:
            line_len = max(1, len(line))

        # Oversized single line: hard-split by chars approximating tokens
        if line_len > token_per_chunk and current_len == 0:
            step = max(50, token_per_chunk)
            for i in range(0, len(line), step):
                chunks.append(line[i : i + step])
            continue

        if current_len + line_len > token_per_chunk and current_len > 0:
            chunks.append(current)
            current = line
            current_len = line_len
        else:
            current = (current + "\n" + line) if current else line
            current_len += line_len

    if current_len > 0:
        chunks.append(current)
    return chunks or [text]


def _parse_full(raw: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (summary, [(speaker, content), ...])."""
    data = _loads_json(raw)
    if data is None:
        return "", []

    summary = ""
    if isinstance(data, dict):
        s = data.get("summary", "")
        if isinstance(s, list):
            summary = "\n".join(str(x) for x in s)
        else:
            summary = str(s).strip()

    turns: list[tuple[str, str]] = []

    if isinstance(data, dict):
        conversations = data.get("conversations", [])
        if conversations and isinstance(conversations, list):
            for item in conversations:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("dialogue", "") or "").strip()
                speaker = str(item.get("said_by", "未知") or "未知").strip() or "未知"
                if content:
                    turns.append((speaker, content))

        if not turns:
            dialogue = str(data.get("对话", "") or "").strip()
            if dialogue:
                turns.append(("未知", dialogue))
            reply = str(data.get("回复", "") or "").strip()
            if reply:
                turns.append(("未知", reply))

    if not turns and isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            content = str(item.get("dialogue") or item.get("对话", "") or "").strip()
            speaker = str(
                item.get("said_by") or item.get("说话人", "未知") or "未知"
            ).strip() or "未知"
            if content:
                turns.append((speaker, content))

    return summary, turns


def _loads_json(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None


if __name__ == "__main__":
    main()
