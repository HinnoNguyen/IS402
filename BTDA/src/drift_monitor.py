"""T41 – Giám sát Prediction Drift & Input/Slang Drift cho credit-intent-endpoint (blue).

Đọc trực tiếp dữ liệu Baseline (Banking77 train.csv) và Production logs (JSONL từ
Azure ML Data Collector: model_outputs, model_inputs), tính các chỉ số drift:
  - Prediction Drift trên cột `label`: Jensen-Shannon Distance, PSI, Chi-squared
  - Input / Slang Drift trên cột `text` & `confidence`: OOV token rate, độ tin cậy trung bình
Đồng thời xuất báo cáo JSON, biểu đồ dashboard PNG và log đầy đủ lên MLflow / Azure ML Studio.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon
from scipy.stats import chisquare

LABELS = ["balance_inquiry", "card_block", "complaint", "fraud", "other"]
TOKEN_RE = re.compile(r"[a-z0-9']+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(str(text).lower())


def load_mdc_outputs(root_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(root_dir.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                ts = str(event.get("time", ""))
                hour_bucket = ts[:13] if len(ts) >= 13 else path.parent.name
                for item in event.get("data", []):
                    if "label" in item:
                        rows.append(
                            {
                                "time": ts,
                                "hour_bucket": hour_bucket,
                                "label": str(item["label"]),
                                "confidence": float(item.get("confidence", np.nan)),
                            }
                        )
    return pd.DataFrame(rows)


def load_mdc_inputs(root_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(root_dir.rglob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                ts = str(event.get("time", ""))
                hour_bucket = ts[:13] if len(ts) >= 13 else path.parent.name
                for item in event.get("data", []):
                    if "text" in item:
                        rows.append(
                            {
                                "time": ts,
                                "hour_bucket": hour_bucket,
                                "text": str(item["text"]),
                            }
                        )
    return pd.DataFrame(rows)


def label_probs(series: pd.Series) -> tuple[np.ndarray, dict[str, int]]:
    counts = series.value_counts().to_dict()
    ordered_counts = {lbl: int(counts.get(lbl, 0)) for lbl in LABELS}
    arr = np.array([ordered_counts[lbl] for lbl in LABELS], dtype=float)
    total = arr.sum()
    probs = arr / total if total > 0 else np.zeros_like(arr)
    return probs, ordered_counts


def compute_psi(base_probs: np.ndarray, prod_probs: np.ndarray, eps: float = 1e-6) -> float:
    b = np.clip(base_probs, eps, 1.0)
    p = np.clip(prod_probs, eps, 1.0)
    b = b / b.sum()
    p = p / p.sum()
    return float(np.sum((p - b) * np.log(p / b)))


def compute_drift_slice(
    name: str,
    base_probs: np.ndarray,
    base_vocab: set[str],
    df_out: pd.DataFrame,
    df_in: pd.DataFrame,
    jsd_threshold: float,
    psi_threshold: float,
) -> dict:
    prod_probs, prod_counts = label_probs(df_out["label"])
    n_out = len(df_out)
    jsd = float(jensenshannon(base_probs, prod_probs, base=2)) if n_out > 0 else 0.0
    psi = compute_psi(base_probs, prod_probs) if n_out > 0 else 0.0

    # Chi-squared goodness-of-fit test against baseline label proportions
    f_obs = np.array([prod_counts[lbl] for lbl in LABELS], dtype=float)
    f_exp = np.clip(base_probs * max(n_out, 1), 1e-6, None)
    f_exp = f_exp * (f_obs.sum() / f_exp.sum()) if f_obs.sum() > 0 else f_exp
    chi2_stat, chi2_p = chisquare(f_obs=f_obs, f_exp=f_exp) if n_out > 0 else (0.0, 1.0)

    confs = df_out["confidence"].dropna() if "confidence" in df_out else pd.Series(dtype=float)
    mean_conf = float(confs.mean()) if len(confs) > 0 else 0.0
    low_conf_rate = float((confs < 0.70).mean()) if len(confs) > 0 else 0.0

    # Vocabulary / slang OOV drift on input texts
    total_tokens = 0
    oov_tokens = 0
    oov_counter: Counter[str] = Counter()
    if len(df_in) > 0:
        for text in df_in["text"]:
            toks = tokenize(text)
            total_tokens += len(toks)
            for tok in toks:
                if tok not in base_vocab:
                    oov_tokens += 1
                    oov_counter[tok] += 1
    oov_rate = float(oov_tokens / total_tokens) if total_tokens > 0 else 0.0

    alert = bool(jsd > jsd_threshold or psi > psi_threshold)
    return {
        "slice": name,
        "output_samples": int(n_out),
        "input_samples": int(len(df_in)),
        "label_counts": prod_counts,
        "label_distribution_pct": {lbl: round(float(p * 100), 2) for lbl, p in zip(LABELS, prod_probs)},
        "jensen_shannon_distance": round(jsd, 6),
        "population_stability_index": round(psi, 6),
        "chi2_stat": round(float(chi2_stat), 4),
        "chi2_p_value": float(chi2_p),
        "mean_confidence": round(mean_conf, 4),
        "low_confidence_rate": round(low_conf_rate, 4),
        "total_tokens": int(total_tokens),
        "oov_tokens": int(oov_tokens),
        "oov_token_rate": round(oov_rate, 4),
        "top_oov_tokens": [[tok, cnt] for tok, cnt in oov_counter.most_common(15)],
        "alert_triggered": alert,
    }


def save_dashboard(
    out_path: Path,
    base_probs: np.ndarray,
    all_res: dict,
    latest_res: dict,
    df_latest_out: pd.DataFrame,
    jsd_threshold: float,
    psi_threshold: float,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(17.5, 5.6))
    fig.patch.set_facecolor("white")

    alert_any = all_res["alert_triggered"] or latest_res["alert_triggered"]
    status_text = "ALERT: PREDICTION DRIFT DETECTED" if alert_any else "STATUS: WITHIN THRESHOLD"
    status_color = "#C62828" if alert_any else "#2E7D32"
    fig.suptitle(
        f"Azure ML Model Monitoring (T41) – credit-intent-endpoint/blue   |   {status_text}",
        fontsize=13,
        fontweight="bold",
        color=status_color,
        y=0.98,
    )

    # Panel 1: Label Distribution Comparison (%)
    ax1 = axes[0]
    x = np.arange(len(LABELS))
    w = 0.27
    base_pct = [p * 100 for p in base_probs]
    all_pct = [all_res["label_distribution_pct"][lbl] for lbl in LABELS]
    latest_pct = [latest_res["label_distribution_pct"][lbl] for lbl in LABELS]

    ax1.bar(x - w, base_pct, width=w, label="Baseline (train.csv)", color="#455A64")
    ax1.bar(x, all_pct, width=w, label=f"Production All (n={all_res['output_samples']})", color="#1E88E5")
    ax1.bar(x + w, latest_pct, width=w, label=f"CFPB + Slang Batch (n={latest_res['output_samples']})", color="#E53935")
    ax1.set_xticks(x)
    ax1.set_xticklabels(LABELS, rotation=22, ha="right", fontsize=9)
    ax1.set_ylabel("Distribution (%)")
    ax1.set_title("1. Label Distribution: Baseline vs Production")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", linestyle="--", alpha=0.4)

    # Panel 2: Drift Metrics vs Thresholds
    ax2 = axes[1]
    metrics_x = np.arange(2)
    mw = 0.32
    all_vals = [all_res["jensen_shannon_distance"], all_res["population_stability_index"]]
    latest_vals = [latest_res["jensen_shannon_distance"], latest_res["population_stability_index"]]

    b1 = ax2.bar(metrics_x - mw / 2, all_vals, width=mw, label="Production All", color="#1E88E5")
    b2 = ax2.bar(metrics_x + mw / 2, latest_vals, width=mw, label="CFPB + Slang Batch", color="#E53935")
    ax2.axhline(jsd_threshold, color="#D84315", linestyle="--", linewidth=1.3, label=f"JSD Threshold ({jsd_threshold})")
    ax2.axhline(psi_threshold, color="#8E24AA", linestyle=":", linewidth=1.5, label=f"PSI Threshold ({psi_threshold})")
    for bars in (b1, b2):
        for bar in bars:
            h = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width() / 2, h + 0.015, f"{h:.3f}", ha="center", va="bottom", fontsize=8.5, fontweight="bold")
    ax2.set_xticks(metrics_x)
    ax2.set_xticklabels(["Jensen-Shannon\nDistance", "Population Stability\nIndex (PSI)"])
    ax2.set_ylabel("Drift Score")
    ax2.set_title("2. Prediction Drift Signals vs Thresholds")
    ax2.set_ylim(0, max(max(all_vals), max(latest_vals), psi_threshold) * 1.25 + 0.05)
    ax2.legend(fontsize=8, loc="upper left")
    ax2.grid(axis="y", linestyle="--", alpha=0.4)

    # Panel 3: Confidence & OOV Vocabulary / Slang Drift
    ax3 = axes[2]
    confs = df_latest_out["confidence"].dropna() if len(df_latest_out) > 0 else pd.Series(dtype=float)
    if len(confs) > 0:
        ax3.hist(confs, bins=12, range=(0.2, 1.0), color="#FB8C00", edgecolor="white", alpha=0.88)
        ax3.axvline(0.70, color="#C62828", linestyle="--", linewidth=1.2, label="Low-conf < 0.70")
    ax3.set_xlabel("Prediction Confidence")
    ax3.set_ylabel("Count")
    ax3.set_title("3. Latest Batch Confidence & Slang OOV Drift")
    ax3.grid(axis="y", linestyle="--", alpha=0.4)
    top_oov = ", ".join(tok for tok, _ in latest_res["top_oov_tokens"][:6]) or "none"
    info_box = (
        f"Latest Hour: {latest_res['slice']}\n"
        f"OOV Token Rate: {latest_res['oov_token_rate']*100:.2f}%\n"
        f"Mean Confidence: {latest_res['mean_confidence']:.3f}\n"
        f"Low Conf (<0.70): {latest_res['low_confidence_rate']*100:.1f}%\n"
        f"Top OOV/Slang: {top_oov}"
    )
    ax3.text(
        0.04,
        0.95,
        info_box,
        transform=ax3.transAxes,
        va="top",
        fontsize=8,
        bbox={"boxstyle": "round,pad=0.4", "facecolor": "#FFF8E1", "edgecolor": "#FFB300"},
    )
    ax3.legend(fontsize=8, loc="lower left")

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline_csv", required=True)
    parser.add_argument("--prod_outputs_dir", required=True)
    parser.add_argument("--prod_inputs_dir", required=True)
    parser.add_argument("--out_dir", default="outputs")
    parser.add_argument("--jsd_threshold", type=float, default=0.10)
    parser.add_argument("--psi_threshold", type=float, default=0.20)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df_base = pd.read_csv(args.baseline_csv)
    base_probs, base_counts = label_probs(df_base["label"])
    base_vocab: set[str] = set()
    for txt in df_base["text"].dropna():
        base_vocab.update(tokenize(txt))

    df_out = load_mdc_outputs(Path(args.prod_outputs_dir))
    df_in = load_mdc_inputs(Path(args.prod_inputs_dir))
    if df_out.empty:
        raise RuntimeError(f"No model_outputs JSONL rows found in {args.prod_outputs_dir}")

    latest_hour = str(df_out["hour_bucket"].max())
    df_out_latest = df_out[df_out["hour_bucket"] == latest_hour].copy()
    df_in_latest = df_in[df_in["hour_bucket"] == latest_hour].copy() if not df_in.empty else df_in

    all_res = compute_drift_slice(
        "all_production",
        base_probs,
        base_vocab,
        df_out,
        df_in,
        args.jsd_threshold,
        args.psi_threshold,
    )
    latest_res = compute_drift_slice(
        latest_hour,
        base_probs,
        base_vocab,
        df_out_latest,
        df_in_latest,
        args.jsd_threshold,
        args.psi_threshold,
    )

    report = {
        "target": "credit-intent-endpoint:blue",
        "baseline": {
            "samples": int(len(df_base)),
            "vocab_size": int(len(base_vocab)),
            "label_counts": base_counts,
            "label_distribution_pct": {lbl: round(float(p * 100), 2) for lbl, p in zip(LABELS, base_probs)},
        },
        "thresholds": {
            "jensen_shannon_distance": args.jsd_threshold,
            "population_stability_index": args.psi_threshold,
            "chi2_alpha": 0.05,
        },
        "all_production": all_res,
        "latest_hour_batch": latest_res,
    }

    report_path = out_dir / "drift_report.json"
    dash_path = out_dir / "drift_dashboard.png"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    save_dashboard(dash_path, base_probs, all_res, latest_res, df_out_latest, args.jsd_threshold, args.psi_threshold)

    print("=== T41 DRIFT MONITORING REPORT ===")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    active = mlflow.active_run()
    ctx = mlflow.start_run(nested=True) if active is not None else mlflow.start_run()
    with ctx:
        mlflow.set_tags(
            {
                "task": "T41",
                "target_endpoint": "credit-intent-endpoint:blue",
                "baseline_asset": "banking77-train-baseline:1",
                "latest_hour_utc": latest_hour,
                "drift_alert_all": str(all_res["alert_triggered"]),
                "drift_alert_latest": str(latest_res["alert_triggered"]),
            }
        )
        mlflow.log_metrics(
            {
                "baseline_samples": len(df_base),
                "prod_all_samples": all_res["output_samples"],
                "prod_all_jsd": all_res["jensen_shannon_distance"],
                "prod_all_psi": all_res["population_stability_index"],
                "prod_all_chi2_p_value": all_res["chi2_p_value"],
                "prod_all_oov_token_rate": all_res["oov_token_rate"],
                "prod_all_mean_confidence": all_res["mean_confidence"],
                "prod_all_alert_triggered": int(all_res["alert_triggered"]),
                "latest_batch_samples": latest_res["output_samples"],
                "latest_batch_jsd": latest_res["jensen_shannon_distance"],
                "latest_batch_psi": latest_res["population_stability_index"],
                "latest_batch_chi2_p_value": latest_res["chi2_p_value"],
                "latest_batch_oov_token_rate": latest_res["oov_token_rate"],
                "latest_batch_mean_confidence": latest_res["mean_confidence"],
                "latest_batch_low_conf_rate": latest_res["low_confidence_rate"],
                "latest_batch_alert_triggered": int(latest_res["alert_triggered"]),
                "jsd_threshold": args.jsd_threshold,
                "psi_threshold": args.psi_threshold,
            }
        )
        mlflow.log_artifact(str(report_path))
        mlflow.log_artifact(str(dash_path))


if __name__ == "__main__":
    main()
