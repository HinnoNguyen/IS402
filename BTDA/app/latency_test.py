"""T44 – Đo độ trễ (latency) của endpoint đặt ở các region khác nhau.

Mỗi target có dạng NHÃN=NGUỒN, NGUỒN là một trong:
  azureml:<endpoint>@<workspace>   online endpoint Azure ML; URL, key và region lấy bằng `az` lúc chạy
  http(s)://...                    URL bất kỳ nhận POST JSON (ví dụ /api/classify của web demo),
                                   key (nếu cần) đặt trong biến môi trường LATENCY_KEY_<NHÃN>

Ví dụ (chạy trong BTDA/app, đã `az login`):
  python latency_test.py ^
      --target japaneast=azureml:credit-intent-endpoint@mlw-credit-assistant ^
      --target southeastasia=azureml:credit-intent-endpoint-sea@mlw-credit-assistant-sea ^
      -n 50

Kết quả: bảng Markdown trên màn hình + outputs/latency/latency_<thời gian>.csv/.md/.png
(thư mục outputs/ đã gitignore; file kết quả chỉ chứa nhãn/region/số đo, không chứa URL hay key).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

APP_DIR = Path(__file__).parent
DEFAULT_REQUEST = APP_DIR.parent / "azureml" / "deployment" / "sample_request.json"


def az(*args: str) -> str:
    exe = shutil.which("az")
    if not exe:
        sys.exit("Không tìm thấy Azure CLI (az). Cài đặt rồi `az login --tenant ms.uit.edu.vn`.")
    out = subprocess.run([exe, *args, "--only-show-errors"], capture_output=True, text=True, encoding="utf-8")
    if out.returncode:
        sys.exit(f"Lệnh az {' '.join(args[:3])} lỗi:\n{out.stderr.strip()}")
    return out.stdout.strip()


def resolve(label: str, spec: str, resource_group: str) -> dict:
    """Trả {label, region, url, headers} cho 1 target."""
    if spec.startswith("azureml:"):
        endpoint, _, workspace = spec.removeprefix("azureml:").partition("@")
        if not workspace:
            sys.exit(f"Target {label}: cần dạng azureml:<endpoint>@<workspace>")
        common = ["-n", endpoint, "-g", resource_group, "-w", workspace]
        info = json.loads(az("ml", "online-endpoint", "show", *common,
                             "--query", "{url:scoring_uri, region:location, traffic:traffic}", "-o", "json"))
        if not any(v > 0 for v in (info.get("traffic") or {}).values()):
            sys.exit(f"Endpoint {endpoint} chưa có deployment nhận traffic – nhờ người phụ trách bật trước khi đo.")
        key = az("ml", "online-endpoint", "get-credentials", *common, "--query", "primaryKey", "-o", "tsv")
        return {"label": label, "region": info["region"], "url": info["url"],
                "headers": {"Authorization": f"Bearer {key}"}}
    if spec.startswith(("http://", "https://")):
        key = os.getenv(f"LATENCY_KEY_{label.upper().replace('-', '_')}")
        return {"label": label, "region": label, "url": spec,
                "headers": {"Authorization": f"Bearer {key}"} if key else {}}
    sys.exit(f"Target {label}: nguồn không hợp lệ {spec!r}")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def measure(target: dict, body: dict | None, n: int, warmup: int, timeout: float, method: str = "POST") -> dict:
    """Gửi warmup + n request tuần tự trên 1 kết nối keep-alive; request đầu tiên đo riêng (gồm DNS + TLS)."""
    samples, errors, first_ms = [], 0, None
    with httpx.Client(timeout=timeout, headers=target["headers"]) as client:
        for i in range(warmup + n):
            start = time.perf_counter()
            try:
                resp = client.request(method, target["url"], json=body if method == "POST" else None)
                ok = resp.status_code < 400 and '"error"' not in resp.text[:200]  # scoring script trả {"error": ...}
            except httpx.HTTPError:
                ok = False
            elapsed = (time.perf_counter() - start) * 1000
            if i == 0:
                first_ms = elapsed
            if i < warmup:
                continue
            if ok:
                samples.append(elapsed)
            else:
                errors += 1
            print(f"\r  {target['label']}: {i + 1 - warmup}/{n}", end="", flush=True)
    print()
    if not samples:
        sys.exit(f"Target {target['label']}: tất cả request đều lỗi – kiểm tra endpoint/key.")
    return {
        "label": target["label"], "region": target["region"], "n": len(samples), "errors": errors,
        "first_ms": round(first_ms, 1), "mean_ms": round(statistics.mean(samples), 1),
        "p50_ms": round(percentile(samples, 50), 1), "p95_ms": round(percentile(samples, 95), 1),
        "min_ms": round(min(samples), 1), "max_ms": round(max(samples), 1), "samples": samples,
    }


COLUMNS = ["label", "region", "n", "errors", "first_ms", "mean_ms", "p50_ms", "p95_ms", "min_ms", "max_ms"]


def to_markdown(results: list[dict]) -> str:
    head = "| Target | Region | Số request | Lỗi | Request đầu (ms) | TB (ms) | p50 (ms) | p95 (ms) | Min | Max |"
    sep = "|" + "---|" * len(COLUMNS)
    rows = [f"| {' | '.join(str(r[c]) for c in COLUMNS)} |" for r in results]
    return "\n".join([head, sep, *rows])


def save(results: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"latency_{datetime.now():%Y%m%d_%H%M%S}"
    with open(stem.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["label", "region", "request_index", "latency_ms"])
        for r in results:
            writer.writerows([r["label"], r["region"], i, round(v, 2)] for i, v in enumerate(r["samples"], 1))
    stem.with_suffix(".md").write_text(to_markdown(results) + "\n", encoding="utf-8")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(Không có matplotlib – bỏ qua biểu đồ. pip install matplotlib để vẽ.)")
        return stem
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    names = [f"{r['label']}\n({r['region']})" for r in results]
    ax1.boxplot([r["samples"] for r in results], showfliers=False)
    ax1.set_xticks(range(1, len(names) + 1), names)
    ax1.set_ylabel("Độ trễ (ms)")
    ax1.set_title("Phân bố độ trễ")
    for r in results:
        ax2.plot(range(1, len(r["samples"]) + 1), r["samples"], marker=".", linewidth=1, label=r["label"])
    ax2.set_xlabel("Request thứ")
    ax2.set_ylabel("Độ trễ (ms)")
    ax2.set_title("Độ trễ theo từng request")
    ax2.legend()
    fig.tight_layout()
    fig.savefig(stem.with_suffix(".png"), dpi=150)
    return stem


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", action="append", required=True, metavar="NHÃN=NGUỒN")
    parser.add_argument("--request-file", default=str(DEFAULT_REQUEST), help="JSON body gửi đi (POST)")
    parser.add_argument("--method", choices=["POST", "GET"], default="POST",
                        help="GET để đo độ trễ mạng thuần tới server, ví dụ /api/health của web demo")
    parser.add_argument("-n", type=int, default=30, help="Số request đo cho mỗi target (mặc định 30)")
    parser.add_argument("--warmup", type=int, default=3, help="Số request khởi động, không tính (mặc định 3)")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("-g", "--resource-group", default="rg-credit-assistant")
    parser.add_argument("--out-dir", default=str(APP_DIR / "outputs" / "latency"))
    args = parser.parse_args()

    body = json.loads(Path(args.request_file).read_text(encoding="utf-8")) if args.method == "POST" else None
    targets = []
    for item in args.target:
        label, sep, spec = item.partition("=")
        if not sep:
            sys.exit(f"--target {item!r}: cần dạng NHÃN=NGUỒN")
        targets.append(resolve(label.strip(), spec.strip(), args.resource_group))

    print(f"Đo {args.n} request {args.method}/target (khởi động {args.warmup}) từ máy này...")
    results = [measure(t, body, args.n, args.warmup, args.timeout, args.method) for t in targets]
    print("\n" + to_markdown(results))
    stem = save(results, Path(args.out_dir))
    print(f"\nĐã lưu: {stem}.csv / .md / .png")


if __name__ == "__main__":
    main()
