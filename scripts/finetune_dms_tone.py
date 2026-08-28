#!/usr/bin/env python3
"""LoRA fine-tune DMS warehouse tone on Qwen (reuses OpenForge HF cache if present)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "training" / "dms_tone.jsonl"
DEFAULT_MODEL = os.environ.get("CORTEX_FINETUNE_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")
SMOKE_MODEL = "Qwen/Qwen2.5-Coder-0.5B-Instruct"


def _load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _format_example(row: dict) -> str:
    parts = []
    for msg in row.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        parts.append(f"<|{role}|>\n{content}")
    parts.append("<|assistant|>\n")
    return "\n".join(parts)


def smoke_check() -> None:
    rows = _load_rows(DEFAULT_DATA)
    assert len(rows) >= 3, "need at least 3 training examples"
    sample = _format_example(rows[0])
    assert "<|assistant|>" in sample
    print(f"OK: {len(rows)} examples, sample len={len(sample)}")


def finetune(*, model_id: str, output_dir: Path, max_steps: int = 50) -> None:
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
        from trl import SFTTrainer
    except ImportError as exc:
        print("Install GPU extras: pip install transformers peft trl datasets accelerate", file=sys.stderr)
        raise SystemExit(1) from exc

    rows = _load_rows(DEFAULT_DATA)
    texts = [_format_example(r) for r in rows]
    ds = Dataset.from_dict({"text": texts})

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Model: {model_id}  device: {device}  steps: {max_steps}")

    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto" if device == "cuda" else None,
        trust_remote_code=True,
    )
    if device == "cpu":
        model = model.to(device)

    lora = LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj", "v_proj"], lora_dropout=0.05, task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)

    args = TrainingArguments(
        output_dir=str(output_dir),
        max_steps=max_steps,
        per_device_train_batch_size=1,
        learning_rate=2e-4,
        logging_steps=5,
        save_steps=max_steps,
        report_to="none",
    )
    trainer = SFTTrainer(model=model, args=args, train_dataset=ds, processing_class=tokenizer)
    trainer.train()
    model.save_pretrained(output_dir / "lora")
    tokenizer.save_pretrained(output_dir / "lora")
    print(f"Saved LoRA adapter to {output_dir / 'lora'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune DMS warehouse tone")
    parser.add_argument("--smoke", action="store_true", help="Validate corpus only")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "training" / "dms_tone_lora")
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--use-smoke-model", action="store_true", help="Use 0.5B for quick GPU test")
    args = parser.parse_args()

    if args.smoke:
        smoke_check()
        return

    model_id = SMOKE_MODEL if args.use_smoke_model else args.model
    args.output.mkdir(parents=True, exist_ok=True)
    finetune(model_id=model_id, output_dir=args.output, max_steps=args.steps)


if __name__ == "__main__":
    main()
