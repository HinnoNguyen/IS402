"""TC 1.3 – Large-scale processing benchmark on the CFPB Consumer Complaint Database (~5.9 GB CSV).

Reads the full CSV in chunks (it does not fit in RAM at once), keeps credit-card complaints and
aggregates them by year and issue, then records wall time, peak memory and throughput so the same
run can be compared between a laptop and the Azure ML cpu-cluster.

    python src/cfpb_profile.py --input data/cfpb --out_dir outputs/cfpb --label laptop
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

USECOLS = ["Date received", "Product", "Issue", "Company", "State", "Submitted via", "Timely response?"]
CREDIT_CARD = "credit card"  # matches "Credit card" and "Credit card or prepaid card"


def find_csv(path: Path) -> Path:
    """Accept a CSV file, or a folder (or its ingest_date=* partitions) that contains one."""
    if path.is_file():
        return path
    candidates = sorted(path.rglob("*.csv"))
    if not candidates:
        raise FileNotFoundError(f"No CSV found under {path}")
    return candidates[-1]  # latest ingest_date partition sorts last


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process: ru_maxrss (KB) on Linux, peak working set on Windows."""
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except ImportError:
        pass
    try:
        import psutil
        info = psutil.Process().memory_info()
        return round(getattr(info, "peak_wset", info.rss) / 2**20, 1)
    except ImportError:
        return None


def machine_info() -> dict:
    info = {"platform": platform.platform(), "python": platform.python_version(),
            "cpu_count": os.cpu_count(), "processor": platform.processor() or platform.machine()}
    try:
        import psutil
        info["ram_total_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except ImportError:
        try:
            with open("/proc/meminfo") as f:
                info["ram_total_gb"] = round(int(f.readline().split()[1]) / 2**20, 1)
        except OSError:
            pass
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="CFPB complaints.csv, or a folder containing it")
    parser.add_argument("--out_dir", default="outputs/cfpb")
    parser.add_argument("--label", default="run", help="Name of the environment, e.g. laptop or azure-cpu-cluster")
    parser.add_argument("--chunksize", type=int, default=500_000)
    args = parser.parse_args()

    csv_path = find_csv(Path(args.input))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    size_bytes = csv_path.stat().st_size
    print(f"Input: {csv_path.name} ({size_bytes / 1e9:.2f} GB) | chunksize {args.chunksize:,}", flush=True)

    total_rows, cc_rows, chunks = 0, 0, 0
    by_product: dict[str, int] = {}
    cc_by_year: dict[int, int] = {}
    cc_by_issue: dict[str, int] = {}
    cc_by_channel: dict[str, int] = {}
    cc_timely_yes = 0

    start = time.perf_counter()
    reader = pd.read_csv(csv_path, usecols=USECOLS, chunksize=args.chunksize, dtype=str, keep_default_na=False)
    for chunk in reader:
        chunks += 1
        total_rows += len(chunk)
        for k, v in chunk["Product"].value_counts().items():
            by_product[k] = by_product.get(k, 0) + int(v)

        cc = chunk[chunk["Product"].str.lower().str.contains(CREDIT_CARD, regex=False)]
        cc_rows += len(cc)
        years = pd.to_datetime(cc["Date received"], errors="coerce").dt.year.dropna().astype(int)
        for k, v in years.value_counts().items():
            cc_by_year[int(k)] = cc_by_year.get(int(k), 0) + int(v)
        for k, v in cc["Issue"].value_counts().items():
            cc_by_issue[k] = cc_by_issue.get(k, 0) + int(v)
        for k, v in cc["Submitted via"].value_counts().items():
            cc_by_channel[k] = cc_by_channel.get(k, 0) + int(v)
        cc_timely_yes += int((cc["Timely response?"] == "Yes").sum())

        elapsed = time.perf_counter() - start
        print(f"  chunk {chunks}: {total_rows:,} rows, {elapsed:.0f}s", flush=True)

    elapsed = time.perf_counter() - start

    # ---- results ----
    pd.Series(cc_by_year, name="complaints").sort_index().rename_axis("year").to_csv(out_dir / "credit_card_by_year.csv")
    (pd.Series(cc_by_issue, name="complaints").sort_values(ascending=False).rename_axis("issue")
     .to_csv(out_dir / "credit_card_by_issue.csv"))
    (pd.Series(by_product, name="complaints").sort_values(ascending=False).rename_axis("product")
     .to_csv(out_dir / "all_products.csv"))

    perf = {
        "label": args.label,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_file": csv_path.name,
        "input_size_gb": round(size_bytes / 1e9, 3),
        "rows_total": total_rows,
        "rows_credit_card": cc_rows,
        "chunks": chunks,
        "chunksize": args.chunksize,
        "elapsed_seconds": round(elapsed, 1),
        "throughput_mb_per_s": round(size_bytes / 1e6 / elapsed, 1),
        "rows_per_second": round(total_rows / elapsed),
        "peak_memory_mb": peak_memory_mb(),
        "machine": machine_info(),
    }
    summary = {
        **perf,
        "credit_card_timely_response_rate": round(cc_timely_yes / cc_rows, 4) if cc_rows else None,
        "credit_card_top_issues": dict(sorted(cc_by_issue.items(), key=lambda kv: -kv[1])[:10]),
        "credit_card_by_channel": dict(sorted(cc_by_channel.items(), key=lambda kv: -kv[1])),
    }
    (out_dir / f"perf_{args.label}.json").write_text(json.dumps(perf, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "cfpb_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.5))
        yrs = pd.Series(cc_by_year).sort_index()
        ax1.bar(yrs.index.astype(str), yrs.values, color="#0E8C88")
        ax1.set_title("Credit card complaints per year (CFPB)")
        ax1.tick_params(axis="x", rotation=45)
        top = pd.Series(cc_by_issue).sort_values().tail(8)
        ax2.barh([t[:45] for t in top.index], top.values, color="#13315C")
        ax2.set_title("Top credit card complaint issues")
        fig.tight_layout()
        fig.savefig(out_dir / "credit_card_overview.png", dpi=150)
    except ImportError:
        print("(matplotlib not installed – chart skipped)")

    print(json.dumps(perf, indent=2, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
