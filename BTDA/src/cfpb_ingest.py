"""Ingest the CFPB Consumer Complaint Database into the raw zone: download the zip, unzip complaints.csv,
write a manifest (source, sizes, timings). Runs as an Azure ML job so the 0.37 GB download and the
5.9 GB unzip happen inside Azure, not over the laptop's network.

    python src/cfpb_ingest.py --out_dir <folder>
"""
import argparse
import json
import shutil
import subprocess
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

URL = "https://files.consumerfinance.gov/ccdb/complaints.csv.zip"


def download(url: str, dest: Path) -> None:
    # The CDN rejects some non-browser clients; curl works, so prefer it and fall back to urllib.
    if shutil.which("curl"):
        subprocess.run(["curl", "-fsSL", "--retry", "3", "-o", str(dest), url], check=True)
        return
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f, length=8 * 2**20)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out_dir", required=True, help="Folder that receives complaints.csv + manifest.json")
    parser.add_argument("--work_dir", default="/tmp/cfpb", help="Local scratch space for the zip")
    parser.add_argument("--url", default=URL)
    args = parser.parse_args()

    out_dir, work = Path(args.out_dir), Path(args.work_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    zip_path = work / "complaints.csv.zip"

    t0 = time.perf_counter()
    download(args.url, zip_path)
    t_download = time.perf_counter() - t0
    print(f"Downloaded {zip_path.stat().st_size / 1e9:.2f} GB in {t_download:.0f}s", flush=True)

    t1 = time.perf_counter()
    with zipfile.ZipFile(zip_path) as z:
        member = next(m for m in z.infolist() if m.filename.endswith(".csv"))
        with z.open(member) as src, open(out_dir / "complaints.csv", "wb") as dst:
            shutil.copyfileobj(src, dst, length=16 * 2**20)
    t_unzip = time.perf_counter() - t1
    csv_size = (out_dir / "complaints.csv").stat().st_size
    print(f"Unzipped {csv_size / 1e9:.2f} GB in {t_unzip:.0f}s", flush=True)

    manifest = {
        "source_url": args.url,
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "zip_size_gb": round(zip_path.stat().st_size / 1e9, 3),
        "csv_size_gb": round(csv_size / 1e9, 3),
        "download_seconds": round(t_download, 1),
        "unzip_seconds": round(t_unzip, 1),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    zip_path.unlink()
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
