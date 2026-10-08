"""Step 2/3 — run the bake-off for one provider (or a model), score every
output, append to results/runs.jsonl and print a per-run line.

Usage:
  python benchmarks/bakeoff/run.py groq
  python benchmarks/bakeoff/run.py gemini --models gemini-3.6-flash,gemini-3.5-flash-lite
  python benchmarks/bakeoff/run.py nvidia --tasks ats_content_optimization,cover_letter
  python benchmarks/bakeoff/run.py all            # every configured candidate (slow)

Structured tasks are run TWICE per model: once plain (production behaviour)
and once with the model's best JSON mode (schema>object) if supported —
so we can see whether structured output improves fidelity.
No production code path is touched.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from benchmarks.bakeoff._common import run_call  # noqa: E402
from benchmarks.bakeoff.score import score  # noqa: E402
from benchmarks.bakeoff.tasks import build_tasks  # noqa: E402

RESULTS = os.path.join(os.path.dirname(__file__), "results")
RUNS = os.path.join(RESULTS, "runs.jsonl")

# candidate models per provider (refined from discovery + capability probes).
# mistral: every model 429s ("code 1300") even sequential -> key has no quota,
#          parked until the user checks it.
CANDIDATES: dict[str, list[str]] = {
    "groq": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"],
    "mistral": ["mistral-small-latest", "mistral-medium-latest", "mistral-medium-3.5"],
    "nvidia": ["nvidia/nemotron-3-super-120b-a12b"],
}

# which models support a strict json_schema response_format (from capability probe)
JSON_SCHEMA_OK = {
    "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b",
    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
    "mistral-small-latest", "mistral-medium-latest", "mistral-medium-3.5",
}
JSON_OBJECT_OK = JSON_SCHEMA_OK | {"nvidia/nemotron-3-super-120b-a12b"}


def _best_json_mode(model: str) -> str:
    if model in JSON_SCHEMA_OK:
        return "json_schema"
    if model in JSON_OBJECT_OK:
        return "json_object"
    return "none"


def _call_with_retry(provider, model, task_id, prompt, **kw):
    for i in range(3):
        r = run_call(provider, model, task_id, prompt, **kw)
        if r.error and ("429" in r.error or "rate" in r.error.lower() or "503" in r.error):
            wait = 10 * (i + 1)
            print(f"      retry {task_id} ({r.error[:50]}) wait {wait}s")
            time.sleep(wait)
            continue
        return r
    return r


def run_provider(provider: str, models: list[str] | None, task_filter: set[str] | None,
                 delay: float = 1.5) -> None:
    models = models or CANDIDATES.get(provider, [])
    tasks = [t for t in build_tasks() if not task_filter or t["id"] in task_filter]
    os.makedirs(RESULTS, exist_ok=True)
    fh = open(RUNS, "a", encoding="utf-8")

    for model in models:
        print(f"\n--- {provider} / {model} ---")
        for t in tasks:
            runs = [{"json_mode": "none", "schema": None}]
            if t["kind"] == "structured":
                jm = _best_json_mode(model)
                if jm != "none":
                    runs.append({"json_mode": jm, "schema": t["json_schema"] if jm == "json_schema" else None})
            for cfg in runs:
                r = _call_with_retry(
                    provider, model, t["id"], t["prompt"],
                    temperature=(0.0 if t["kind"] == "structured" else 0.3),
                    max_tokens=t["max_tokens"],
                    json_mode=cfg["json_mode"], json_schema=cfg["schema"],
                    stream_ttft=True,
                )
                if r.ok:
                    r.scores = score(t["id"], r.text)
                row = r.slim()
                row["kind"] = t["kind"]
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                hf = r.scores.get("hallucination_free")
                print(f"  {t['id']:26} mode={cfg['json_mode']:11} "
                      f"{'OK ' if r.ok else 'FAIL':4} "
                      f"wall={r.wall_s:6.1f}s ttft={str(r.ttft_s or '-'):5} "
                      f"tok/s={str(r.tok_per_s or '-'):6} "
                      f"jsonV={r.json_valid} "
                      f"halluc_free={hf} "
                      f"{('ERR ' + str(r.error)[:60]) if r.error else ''}")
                time.sleep(delay)
    fh.close()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    which = sys.argv[1]
    models = None
    task_filter = None
    for a in sys.argv[2:]:
        if a.startswith("--models"):
            models = a.split("=", 1)[1].split(",") if "=" in a else sys.argv[sys.argv.index(a) + 1].split(",")
        if a.startswith("--tasks"):
            task_filter = set(a.split("=", 1)[1].split(",") if "=" in a else
                              sys.argv[sys.argv.index(a) + 1].split(","))

    provs = list(CANDIDATES) if which == "all" else [which]
    for p in provs:
        run_provider(p, models if which != "all" else None, task_filter,
                     delay=(0.8 if p in ("groq", "gemini") else 2.0))
    print(f"\nappended to {RUNS}")


if __name__ == "__main__":
    main()
