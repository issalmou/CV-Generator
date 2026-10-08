"""Aggregate results/runs.jsonl into:
  - a per (task, model) comparison table (markdown) -> results/report.md
  - the raw writing/chat outputs for human quality review -> results/human_review.md
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

R = os.path.join(os.path.dirname(__file__), "results")
RUNS = os.path.join(R, "runs.jsonl")


def load() -> list[dict]:
    return [json.loads(l) for l in open(RUNS, encoding="utf-8")]


def _q(row: dict) -> str:
    """Compact quality verdict from the automated scores."""
    s = row.get("scores") or {}
    if not row["ok"]:
        return "FAIL"
    bits = []
    if s.get("hallucination_free") is True:
        bits.append("no-halluc")
    elif s.get("hallucination_free") is False:
        h = s.get("hallucinations") or s.get("summary_invented_numbers") or s.get("invented_numbers") or []
        bits.append(f"HALLUC({','.join(map(str, h))[:40]})")
    if s.get("additional_skills_already_owned"):
        bits.append(f"add_skills_owned({s['additional_skills_already_owned']})")
    if "count_ok" in s:
        bits.append("count-ok" if s["count_ok"] else f"count={s.get('count')}!")
    if "coverage" in s:
        bits.append(f"kw-cov={s['coverage']}")
    if s.get("missing_keys"):
        bits.append(f"missing_keys={s['missing_keys']}")
    if s.get("paragraphs_ok") is False:
        bits.append(f"{s.get('paragraphs')}-paras!")
    if s.get("placeholders"):
        bits.append(f"placeholders{s['placeholders']}")
    if s.get("mentions_company") is False:
        bits.append("no-company")
    return " ".join(bits) or "ok"


def main() -> None:
    rows = load()
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)

    md = ["# Bake-off — automated comparison\n",
          f"_{len(rows)} runs · sample CV + real JD · temperature 0 for structured, 0.3 for writing_\n"]
    for task in sorted(by_task):
        md.append(f"\n## {task}\n")
        md.append("| provider | model | mode | ok | wall s | ttft | tok/s | in→out tok | JSON | quality (auto) |")
        md.append("|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(by_task[task], key=lambda x: (x["provider"], x["model"], x["json_mode"])):
            io = f"{r.get('prompt_tokens')}→{r.get('completion_tokens')}"
            md.append(f"| {r['provider']} | {r['model']} | {r['json_mode']} | "
                      f"{'✅' if r['ok'] else '❌'} | {r['wall_s']:.1f} | {r.get('ttft_s') or '-'} | "
                      f"{r.get('tok_per_s') or '-'} | {io} | {r.get('json_valid')} | {_q(r)} |")
    open(os.path.join(R, "report.md"), "w", encoding="utf-8").write("\n".join(md))

    # human review: the writing + chat outputs
    hr = ["# Human quality review — writing / chat outputs\n",
          "Read for: fidelity to the source CV, hallucinations, ATS relevance, "
          "writing quality, prompt-constraint compliance.\n"]
    for task in ("ats_content_optimization", "cover_letter", "conversation_agent",
                 "profile_analysis"):
        hr.append(f"\n\n{'='*90}\n# {task}\n{'='*90}")
        for r in by_task.get(task, []):
            if not r["ok"]:
                continue
            hr.append(f"\n\n--- {r['provider']} / {r['model']} (mode={r['json_mode']}, "
                      f"{r['wall_s']:.1f}s, {r.get('completion_tokens')} tok) ---\n")
            hr.append(r["text"][:5000])
    open(os.path.join(R, "human_review.md"), "w", encoding="utf-8").write("\n".join(hr))
    print(f"wrote {R}/report.md and {R}/human_review.md")


if __name__ == "__main__":
    main()
