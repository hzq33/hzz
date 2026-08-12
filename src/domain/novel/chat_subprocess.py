"""General-purpose chat subprocess — raw model output for any task.

Reads JSON from stdin or file: {"system": "...", "text": "...", "model_path": "..."}
Returns: {"response": "...", "error": null}
"""

import json
import sys

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
        system_prompt = req.get("system", "You are a helpful assistant.")
        text = req["text"]
        model_path = req.get("model_path", "models/Haruhi-Dialogue-Speaker-Extract_qwen18")
    except Exception as e:
        json.dump({"response": "", "error": str(e)}, sys.stdout)
        sys.exit(1)

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        global _MODEL, _TOKENIZER
        if _MODEL is None:
            _TOKENIZER = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
            bnb = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
            )
            _MODEL = AutoModelForCausalLM.from_pretrained(
                model_path, trust_remote_code=True,
                quantization_config=bnb, device_map="auto",
            )
            _MODEL.eval()

        resp, _ = _MODEL.chat(_TOKENIZER, text, history=[], system=system_prompt)
        json.dump({"response": resp, "error": None}, sys.stdout, ensure_ascii=False)
        sys.exit(0)
    except Exception as e:
        json.dump({"response": "", "error": str(e)}, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
