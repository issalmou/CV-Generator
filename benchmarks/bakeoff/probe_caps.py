"""Step 1b — capability probe for a curated shortlist of chat/instruct models
(the discovery listed 81/55/46/14 models incl. embeddings/vision/audio; here
we probe only plausible text-generation candidates).

Probes per model: plain call (ttft, tok/s), json_object mode, json_schema
strict mode. Writes results/capabilities.json + prints a table.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from benchmarks.bakeoff.discover import probe_capabilities  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "results", "capabilities.json")

SHORTLIST: dict[str, list[str]] = {
    "nvidia": [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "nvidia/nemotron-nano-3-30b-a3b",
        "writer/palmyra-creative-122b",
        "openai/gpt-oss-20b",
    ],
    "gemini": [
        "gemini-flash-latest",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-3-flash-preview",
    ],
    "mistral": [
        "mistral-small-latest",
        "mistral-medium-latest",
        "mistral-medium-3.5",
        "magistral-small-latest",
    ],
    "groq": [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
    ],
}


def main() -> None:
    jobs = [(p, m) for p, ms in SHORTLIST.items() for m in ms]
    results: list[dict] = []
    # providers are independent APIs -> run in parallel, but cap concurrency
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(probe_capabilities, p, m): (p, m) for p, m in jobs}
        for fut in concurrent.futures.as_completed(futs):
            p, m = futs[fut]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                r = {"provider": p, "model": m, "plain_ok": False, "plain_err": str(e)[:200]}
            results.append(r)
            print(f"  done: {p}/{m}")

    results.sort(key=lambda r: (r["provider"], r["model"]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": results},
                  f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 110)
    print(f"{'provider':8} {'model':40} {'plain':6} {'ttft':6} {'tok/s':7} {'json_obj':9} {'json_schema':11}")
    print("-" * 110)
    for r in results:
        print(f"{r['provider']:8} {r['model']:40} "
              f"{'ok' if r.get('plain_ok') else 'FAIL':6} "
              f"{str(r.get('ttft_s') or '-'):6} "
              f"{str(r.get('tok_per_s') or '-'):7} "
              f"{'yes' if r.get('json_object_ok') else 'no':9} "
              f"{'yes' if r.get('json_schema_ok') else 'no':11}")
        if not r.get("plain_ok"):
            print(f"         -> {r.get('plain_err')}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
