"""T43 – Azure Function (FaaS) xử lý phản hồi "Không thích" của người dùng.

Đọc container Cosmos DB `feedback` (web ghi khi người dùng bấm 👍/👎), lấy các phản hồi rating = "down",
gom theo câu hỏi và gợi ý hướng xử lý cho nhóm:
  - chatbot trả lời "không có thông tin"  -> bổ sung tài liệu RAG
  - chatbot bị bộ lọc an toàn chặn       -> xem lại bộ lọc / câu hỏi hợp lệ bị chặn nhầm
  - chatbot trả lời nhưng chưa đúng ý     -> sửa prompt hoặc đoạn truy xuất
  - mô hình phân loại sai nhãn             -> thêm câu vào dữ liệu huấn luyện, huấn luyện lại
Báo cáo ghi vào Data Lake: processed/feedback/report_date=YYYY-MM-DD/dislikes.csv + dislikes.json.

Hai trigger:
  - Timer: 18:30 UTC hằng ngày (01:30 giờ Việt Nam), sau pipeline ETL 01:00
  - HTTP GET /api/feedback-report (cần function key): chạy ngay và trả về bản tóm tắt

Xác thực bằng managed identity của Function App (DefaultAzureCredential), không có key trong code:
  Cosmos DB Built-in Data Reader trên database credit-assistant, Storage Blob Data Contributor trên Data Lake.
"""
from __future__ import annotations

import collections
import csv
import io
import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

NO_INFO = ("không có thông tin", "chưa có thông tin", "không tìm thấy")


def suggest(kind: str, answer: str, blocked: bool) -> str:
    if kind == "classify":
        return "Phân loại sai: thêm câu vào dữ liệu huấn luyện, huấn luyện lại"
    if blocked:
        return "Bị bộ lọc an toàn chặn: kiểm tra câu hỏi hợp lệ có bị chặn nhầm"
    if any(k in (answer or "").lower() for k in NO_INFO):
        return "Thiếu thông tin: bổ sung tài liệu RAG"
    return "Trả lời chưa đúng ý: xem lại prompt hoặc đoạn truy xuất"


def build_report() -> dict:
    cred = DefaultAzureCredential()
    container = (CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cred)
                 .get_database_client(os.environ.get("COSMOS_DATABASE", "credit-assistant"))
                 .get_container_client(os.environ.get("COSMOS_CONTAINER", "feedback")))
    docs = list(container.query_items(
        "SELECT c.kind, c.question, c.answer, c.sources, c.blocked, c.confidence, c.comment, c.created_at "
        "FROM c WHERE c.rating = 'down'", enable_cross_partition_query=True))

    groups: dict[tuple, dict] = {}
    for d in docs:
        kind = d.get("kind") or "chat"
        key = (kind, " ".join((d.get("question") or "").lower().split()))
        g = groups.setdefault(key, {"kind": kind, "question": d.get("question", ""), "count": 0,
                                    "last_answer": "", "last_at": "", "comments": [], "sources": collections.Counter()})
        g["count"] += 1
        if (d.get("created_at") or "") >= g["last_at"]:
            g["last_at"], g["last_answer"], g["blocked"] = d.get("created_at") or "", d.get("answer", ""), bool(d.get("blocked"))
        if d.get("comment"):
            g["comments"].append(d["comment"])
        g["sources"].update(s.split(" – ")[0] for s in d.get("sources") or [])

    rows = []
    for g in sorted(groups.values(), key=lambda g: (-g["count"], g["question"])):
        rows.append({
            "kind": g["kind"], "question": g["question"], "dislikes": g["count"],
            "suggestion": suggest(g["kind"], g["last_answer"], g.get("blocked", False)),
            "last_answer": g["last_answer"][:300], "top_sources": "; ".join(s for s, _ in g["sources"].most_common(3)),
            "comments": " | ".join(g["comments"][:5]), "last_at": g["last_at"],
        })
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total_dislikes": len(docs), "questions": len(rows),
            "by_suggestion": dict(collections.Counter(r["suggestion"] for r in rows)), "rows": rows}


def save_report(report: dict) -> str:
    account = os.environ["DATALAKE_ACCOUNT"]
    blob = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential())
    prefix = f"feedback/report_date={report['generated_at'][:10]}"
    buf = io.StringIO()
    fields = ["kind", "question", "dislikes", "suggestion", "last_answer", "top_sources", "comments", "last_at"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(report["rows"])
    container = blob.get_container_client(os.environ.get("DATALAKE_CONTAINER", "processed"))
    container.upload_blob(f"{prefix}/dislikes.csv", ("﻿" + buf.getvalue()).encode("utf-8"), overwrite=True)
    container.upload_blob(f"{prefix}/dislikes.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"), overwrite=True)
    return prefix


@app.timer_trigger(schedule="0 30 18 * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def feedback_report_daily(timer: func.TimerRequest) -> None:
    report = build_report()
    prefix = save_report(report)
    logging.info("Feedback report: %s phản hồi Không thích, %s câu hỏi -> %s", report["total_dislikes"], report["questions"], prefix)


@app.route(route="feedback-report", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def feedback_report_http(req: func.HttpRequest) -> func.HttpResponse:
    report = build_report()
    prefix = save_report(report)
    summary = {k: report[k] for k in ("generated_at", "total_dislikes", "questions", "by_suggestion")}
    summary["saved_to"] = f"{os.environ.get('DATALAKE_CONTAINER', 'processed')}/{prefix}/"
    summary["top"] = [{k: r[k] for k in ("kind", "question", "dislikes", "suggestion")} for r in report["rows"][:10]]
    return func.HttpResponse(json.dumps(summary, ensure_ascii=False, indent=2), mimetype="application/json")
