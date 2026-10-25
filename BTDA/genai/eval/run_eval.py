"""T31 – GenAI quality gate: run the flow on eval_data.jsonl, then score Groundedness / Relevance / Coherence
(+ Similarity to the reference answer) with azure-ai-evaluation and log the run to the Foundry project.

Gate: mean Groundedness > 4 (scale 1-5). Keys are read through the Azure CLI (`az login` first).

    python eval/run_eval.py                 # full run, results logged to proj-credit-assistant
    python eval/run_eval.py --no-upload     # local only
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PF_DISABLE_TRACING", "true")
from azure.ai.evaluation import (AzureOpenAIModelConfiguration, CoherenceEvaluator, GroundednessEvaluator,  # noqa: E402
                                 RelevanceEvaluator, SimilarityEvaluator, evaluate)
from promptflow.client import load_flow  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FLOW = ROOT / "promptflow" / "credit-assistant"
DATA = ROOT / "eval" / "eval_data.jsonl"
OUT = ROOT / "eval" / "results"
RG, PROJECT, AIS = "rg-credit-assistant", "proj-credit-assistant", "ais-credit-assistant"
JUDGE_DEPLOYMENT = "gpt-4.1-mini"
GROUNDEDNESS_GATE = 4.0


def az(*args):
    exe = shutil.which("az") or shutil.which("az.cmd")
    return subprocess.run([exe, *args, "-o", "tsv"], check=True, capture_output=True, text=True).stdout.strip()


def generate_responses(path: Path) -> None:
    flow = load_flow(str(FLOW))
    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    with path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            out = flow(question=row["question"], chat_history=[])
            f.write(json.dumps({**row, "answer": out["answer"], "context": out["context"]}, ensure_ascii=False) + "\n")
            print(f"  [{i}/{len(rows)}] {row['question'][:70]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-upload", action="store_true", help="do not log the run to the Foundry project")
    p.add_argument("--reuse-responses", action="store_true", help="skip step 1 and reuse results/responses.jsonl")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    responses = OUT / "responses.jsonl"

    if not args.reuse_responses:
        print("1) Running the flow on the evaluation set ...")
        generate_responses(responses)

    print("2) Scoring with azure-ai-evaluation ...")
    judge = AzureOpenAIModelConfiguration(
        azure_endpoint=f"https://{AIS}.openai.azure.com/", azure_deployment=JUDGE_DEPLOYMENT, api_version="2024-10-21",
        api_key=az("cognitiveservices", "account", "keys", "list", "-n", AIS, "-g", RG, "--query", "key1"))
    qa = {"query": "${data.question}", "response": "${data.answer}"}
    project = None if args.no_upload else {"subscription_id": az("account", "show", "--query", "id"),
                                           "resource_group_name": RG, "project_name": PROJECT}
    result = evaluate(
        evaluation_name="credit-assistant-rag-eval",
        data=str(responses),
        evaluators={"groundedness": GroundednessEvaluator(judge), "relevance": RelevanceEvaluator(judge),
                    "coherence": CoherenceEvaluator(judge), "similarity": SimilarityEvaluator(judge)},
        evaluator_config={
            "groundedness": {"column_mapping": {**qa, "context": "${data.context}"}},
            "relevance": {"column_mapping": qa},
            "coherence": {"column_mapping": qa},
            "similarity": {"column_mapping": {**qa, "ground_truth": "${data.ground_truth}"}},
        },
        azure_ai_project=project,
        output_path=str(OUT / "eval_result.json"),
    )

    metrics = result["metrics"]
    score = lambda name: metrics.get(f"{name}.{name}", metrics.get(f"{name}.gpt_{name}"))  # noqa: E731
    summary = {name: score(name) for name in ("groundedness", "relevance", "coherence", "similarity")}
    passed = (summary["groundedness"] or 0) > GROUNDEDNESS_GATE
    summary.update(rows=len(result["rows"]), groundedness_gate=GROUNDEDNESS_GATE, quality_gate_passed=passed,
                   studio_url=result.get("studio_url"))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not passed:
        raise SystemExit(f"Quality gate FAILED: groundedness {summary['groundedness']} <= {GROUNDEDNESS_GATE}")


if __name__ == "__main__":
    main()
