"""Re-probe Gemini (2026 model names) and Mistral (sequential + backoff)."""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from benchmarks.bakeoff._common import client_for  # noqa: E402
from benchmarks.bakeoff.discover import probe_capabilities  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "results", "capabilities_reprobe.json")

GEMINI = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview", "gemini-pro-latest", "gemini-3.1-flash-lite",
]
MISTRAL = ["mistral-small-latest", "mistral-medium-latest", "mistral-medium-3.5", "magistral-small-latest"]


def _with_retry(provider, model, tries=4):
    for i in range(tries):
        r = probe_capabilities(provider, model)
        err = (r.get("plain_err") or "")
        if "429" in err or "rate" in err.lower() or "503" in err:
            wait = 8 * (i + 1)
            print(f"    {provider}/{model}: {err[:60]} -> wait {wait}s")
            time.sleep(wait)
            continue
        return r
    return r


def main():
    results = []
    print("=== Mistral list (fresh) ===")
    cli, base, ok = client_for("mistral")
    try:
        ids = sorted({m.id for m in cli.models.list().data})
        print(f"  {len(ids)} models")
    except Exception as e:
        print("  list error:", str(e)[:150])

    print("\n=== Gemini (2026 names) ===")
    for m in GEMINI:
        r = _with_retry("gemini", m)
        results.append(r)
        print(f"  {m:26} plain={'ok' if r.get('plain_ok') else 'FAIL'} "
              f"ttft={r.get('ttft_s')} tok/s={r.get('tok_per_s')} "
              f"json_obj={r.get('json_object_ok')} json_schema={r.get('json_schema_ok')}")
        if not r.get("plain_ok"):
            print(f"     -> {r.get('plain_err')}")
        time.sleep(2)

    print("\n=== Mistral (sequential + backoff) ===")
    for m in MISTRAL:
        r = _with_retry("mistral", m)
        results.append(r)
        print(f"  {m:26} plain={'ok' if r.get('plain_ok') else 'FAIL'} "
              f"ttft={r.get('ttft_s')} tok/s={r.get('tok_per_s')} "
              f"json_obj={r.get('json_object_ok')} json_schema={r.get('json_schema_ok')}")
        if not r.get("plain_ok"):
            print(f"     -> {r.get('plain_err')}")
        time.sleep(5)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
