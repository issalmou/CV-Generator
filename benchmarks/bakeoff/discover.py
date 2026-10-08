"""Step 1 — discover the models actually available with each .env key,
plus a light capability probe (json_object / json_schema support, a small
latency+tok/s sample). Writes results/discovery.json.

No production change. Read-only against the provider APIs.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from benchmarks.bakeoff._common import PROVIDERS, client_for, run_call  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "results", "discovery.json")

_TINY_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "population": {"type": "integer"}},
    "required": ["city", "population"],
    "additionalProperties": False,
}
_TINY_PROMPT = 'Return the capital of France and a rough population as JSON with keys "city" and "population".'


def list_models(provider: str) -> tuple[list[str], str | None]:
    cli, base, configured = client_for(provider)
    if not configured:
        return [], "no api key in .env"
    try:
        page = cli.models.list()
        ids = sorted({m.id for m in page.data})
        return ids, None
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {str(e)[:200]}"


def probe_capabilities(provider: str, model: str) -> dict:
    out: dict = {"provider": provider, "model": model}
    # plain
    r = run_call(provider, model, "cap_plain", _TINY_PROMPT, max_tokens=200, stream_ttft=True)
    out["plain_ok"] = r.ok
    out["plain_err"] = r.error
    out["ttft_s"] = r.ttft_s
    out["wall_s"] = r.wall_s
    out["tok_per_s"] = r.tok_per_s
    out["out_tokens"] = r.completion_tokens
    # json_object
    r2 = run_call(provider, model, "cap_json_object", _TINY_PROMPT, max_tokens=200,
                  json_mode="json_object", stream_ttft=False)
    out["json_object_ok"] = bool(r2.ok and r2.json_valid)
    out["json_object_err"] = r2.error
    # json_schema (strict)
    r3 = run_call(provider, model, "cap_json_schema", _TINY_PROMPT, max_tokens=200,
                  json_mode="json_schema", json_schema=_TINY_SCHEMA, stream_ttft=False)
    out["json_schema_ok"] = bool(r3.ok and r3.json_valid)
    out["json_schema_err"] = r3.error
    return out


def main() -> None:
    result: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "providers": {}}
    for p in PROVIDERS:
        print(f"\n=== {p} ===")
        ids, err = list_models(p)
        print(f"  models endpoint: {'ERROR ' + err if err else str(len(ids)) + ' models'}")
        entry: dict = {"error": err, "model_count": len(ids), "models": ids, "capabilities": []}
        result["providers"][p] = entry
        if err:
            continue
        # print the full list so we can pick candidates
        for mid in ids:
            print("   ", mid)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
