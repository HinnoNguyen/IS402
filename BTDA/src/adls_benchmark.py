"""T20 / TC 3.1 – Đo tốc độ đọc ghi giữa máy chạy script và Data Lake (ADLS Gen2 stcredit23520121).

Upload rồi download các file ngẫu nhiên (không nén được) nhiều kích thước, lặp lại nhiều lần, đo MB/s.
Thêm phép so sánh upload/download tuần tự (1 luồng) và song song (nhiều luồng) cho mục tối ưu.
File test nằm ở raw/_benchmark/<label>/ và bị xóa ngay sau khi đo.

    # Laptop (đăng nhập bằng az login):
    python src/adls_benchmark.py --label laptop
    # Trong job Azure ML: tự dùng managed identity của cluster (biến DEFAULT_IDENTITY_CLIENT_ID)
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from azure.storage.blob import BlobServiceClient

ACCOUNT = "stcredit23520121"
CONTAINER = "raw"
MB = 1024 * 1024


def credential():
    if os.getenv("AZUREML_RUN_ID") or os.getenv("DEFAULT_IDENTITY_CLIENT_ID"):
        # Azure ML compute: managed identity of the cluster (client_id only needed for user-assigned identities)
        from azure.identity import ManagedIdentityCredential
        return ManagedIdentityCredential(client_id=os.getenv("DEFAULT_IDENTITY_CLIENT_ID"))
    from azure.identity import AzureCliCredential
    return AzureCliCredential()


def timed(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--label", default="laptop")
    p.add_argument("--sizes", default="1,10,100,300", help="MB, phân tách bằng dấu phẩy")
    p.add_argument("--repeats", default="3,3,2,1", help="số lần lặp cho từng kích thước")
    p.add_argument("--parallel_size", type=int, default=100, help="MB dùng cho phép so sánh 1 luồng / nhiều luồng")
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--out_dir", default="outputs/adls_benchmark")
    args = p.parse_args()

    sizes = [int(x) for x in args.sizes.split(",")]
    repeats = [int(x) for x in args.repeats.split(",")]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    svc = BlobServiceClient(f"https://{ACCOUNT}.blob.core.windows.net", credential=credential())
    container = svc.get_container_client(CONTAINER)
    prefix = f"_benchmark/{args.label}-{datetime.now():%Y%m%d%H%M%S}"
    rows = []

    def measure(size_mb, rep, threads, kind):
        data = os.urandom(size_mb * MB)
        blob = container.get_blob_client(f"{prefix}/{kind}_{size_mb}mb_{rep}_{threads}t.bin")
        up = timed(lambda: blob.upload_blob(data, overwrite=True, max_concurrency=threads))
        down = timed(lambda: blob.download_blob(max_concurrency=threads).readall())
        blob.delete_blob()
        for op, sec in (("upload", up), ("download", down)):
            rows.append({"label": args.label, "kind": kind, "op": op, "size_mb": size_mb, "threads": threads,
                         "seconds": round(sec, 3), "mb_per_s": round(size_mb / sec, 2)})
        print(f"  {kind:8} {size_mb:>4} MB x{threads:<2} rep{rep}: upload {size_mb / up:7.1f} MB/s | download {size_mb / down:7.1f} MB/s", flush=True)

    print(f"[{args.label}] ADLS {ACCOUNT}/{CONTAINER}/{prefix}")
    for size, rep_count in zip(sizes, repeats):
        for rep in range(1, rep_count + 1):
            measure(size, rep, args.threads, "size")
    for threads in (1, args.threads):
        measure(args.parallel_size, 1, threads, "parallel")

    summary = []
    for kind in ("size", "parallel"):
        keys = sorted({(r["op"], r["size_mb"], r["threads"]) for r in rows if r["kind"] == kind}, key=lambda k: (k[0], k[1], k[2]))
        for op, size, threads in keys:
            vals = [r["mb_per_s"] for r in rows if (r["kind"], r["op"], r["size_mb"], r["threads"]) == (kind, op, size, threads)]
            summary.append({"label": args.label, "kind": kind, "op": op, "size_mb": size, "threads": threads,
                            "runs": len(vals), "mean_mb_per_s": round(statistics.mean(vals), 2)})
    result = {"label": args.label, "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "platform": platform.platform(), "cpu_count": os.cpu_count(), "summary": summary, "runs": rows}
    (out / f"adls_benchmark_{args.label}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
