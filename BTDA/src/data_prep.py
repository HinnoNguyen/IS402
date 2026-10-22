"""DataOps: load Banking77, run data quality checks, clean text, map 77 intents -> 5 business groups,
save processed CSVs + a quality report. Runs locally or as an Azure ML pipeline step."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {"text", "category"}


def clean_text(text: str) -> str:
    text = str(text).lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def load_mapping(mapping_path: Path) -> tuple[dict, str]:
    cfg = json.loads(mapping_path.read_text(encoding="utf-8"))
    lookup = {intent: group for group, intents in cfg["mapping"].items() for intent in intents}
    return lookup, cfg.get("version", "unknown")


def resolve_raw_dir(raw_dir: Path) -> tuple[Path, str]:
    """Accept either a folder with train.csv/test.csv, or a parent folder with ingest_date=YYYY-MM-DD
    partitions (pick the latest one)."""
    if (raw_dir / "train.csv").exists():
        return raw_dir, raw_dir.name
    partitions = sorted(p for p in raw_dir.glob("ingest_date=*") if p.is_dir())
    if not partitions:
        raise FileNotFoundError(f"No train.csv or ingest_date=* partitions found in {raw_dir}")
    return partitions[-1], partitions[-1].name


def quality_checks(df: pd.DataFrame, split: str, known_intents: set | None) -> dict:
    """Fail fast on broken input; report softer issues (nulls, duplicates) in the quality report."""
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"[{split}] missing required columns: {missing}")
    if df.empty:
        raise ValueError(f"[{split}] dataset is empty")

    report = {
        "rows_raw": int(len(df)),
        "null_text": int(df["text"].isna().sum()),
        "null_category": int(df["category"].isna().sum()),
        "duplicate_text": int(df["text"].duplicated().sum()),
    }
    if known_intents is not None:
        unknown = sorted(set(df["category"].dropna()) - known_intents)
        report["unknown_intents"] = unknown
        if unknown:
            raise ValueError(f"[{split}] unknown intents not in categories.json: {unknown}")
    return report


def process(df: pd.DataFrame, lookup: dict) -> pd.DataFrame:
    df = df.dropna(subset=["text", "category"]).drop_duplicates(subset=["text"]).copy()
    df["text"] = df["text"].map(clean_text)
    df = df[df["text"].str.len() > 0]
    df["label"] = df["category"].map(lookup).fillna("other")
    return df[["text", "category", "label"]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw_dir", default="banking_data")
    parser.add_argument("--mapping", default="config/intent_mapping.json")
    parser.add_argument("--out_dir", default="data/processed")
    args = parser.parse_args()

    raw_dir, partition = resolve_raw_dir(Path(args.raw_dir))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lookup, mapping_version = load_mapping(Path(args.mapping))

    categories_file = raw_dir / "categories.json"
    known_intents = set(json.loads(categories_file.read_text())) if categories_file.exists() else None

    report = {
        "processed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_partition": partition,
        "mapping_version": mapping_version,
        "splits": {},
    }
    for split in ("train", "test"):
        raw = pd.read_csv(raw_dir / f"{split}.csv")
        split_report = quality_checks(raw, split, known_intents)
        df = process(raw, lookup)
        df.to_csv(out_dir / f"{split}.csv", index=False)
        split_report["rows_processed"] = int(len(df))
        split_report["label_counts"] = {k: int(v) for k, v in df["label"].value_counts().items()}
        report["splits"][split] = split_report
        print(f"[{split}] {len(df)} rows")
        print(df["label"].value_counts().to_string(), "\n")

    (out_dir / "quality_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Source partition: {partition} | mapping {mapping_version} | report -> {out_dir / 'quality_report.json'}")


if __name__ == "__main__":
    main()
