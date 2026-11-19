"""Web demo Trợ lý tín dụng (T36, T37).

FastAPI phục vụ giao diện tĩnh trong `static/` và làm proxy tới 2 Azure ML managed online endpoint:
  - CLASSIFIER_*: model phân loại ý định (BTDA/azureml/deployment/main.py) – nhận {"data": [...]}
  - CHAT_*:       Prompt Flow RAG chatbot (BTDA/genai/promptflow/credit-assistant) – nhận {"question", "chat_history"}

Key của endpoint chỉ nằm ở backend (biến môi trường / file .env không commit), trình duyệt không thấy.
Endpoint nào chưa cấu hình URL thì chạy chế độ MOCK (dữ liệu giả) để web vẫn chạy được ở local.

    uvicorn main:app --reload        # chạy trong thư mục BTDA/app
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).parent
load_dotenv(APP_DIR / ".env")
log = logging.getLogger("credit-assistant-web")

# Nhãn tiếng Việt – giữ khớp `labels_vi` trong BTDA/config/intent_mapping.json
LABELS_VI = {
    "complaint": "Khiếu nại",
    "balance_inquiry": "Hỏi đáp số dư",
    "card_block": "Yêu cầu khóa thẻ",
    "fraud": "Gian lận",
    "other": "Khác",
}


@dataclass(frozen=True)
class Endpoint:
    """Cấu hình 1 online endpoint, đọc từ biến môi trường <PREFIX>_URL / _KEY / _DEPLOYMENT."""

    name: str
    url: str
    key: str
    deployment: str  # rỗng = để endpoint tự chia traffic (blue/green)

    @classmethod
    def from_env(cls, prefix: str) -> "Endpoint":
        get = lambda k: os.getenv(f"{prefix}_{k}", "").strip()  # noqa: E731
        return cls(prefix.lower(), get("URL"), get("KEY"), get("DEPLOYMENT"))

    @property
    def live(self) -> bool:
        return bool(self.url) and os.getenv("MOCK_MODE", "").lower() not in ("1", "true", "yes")

    def headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"}
        if self.deployment:
            headers["azureml-model-deployment"] = self.deployment
        return headers


TIMEOUT = float(os.getenv("ENDPOINT_TIMEOUT_SECONDS", "60"))


async def call_endpoint(ep: Endpoint, payload: dict) -> dict:
    """POST tới endpoint, trả JSON. Lỗi mạng/HTTP -> 502 kèm thông báo ngắn (không lộ URL/key)."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(ep.url, headers=ep.headers(), json=payload)
    except httpx.HTTPError as ex:
        log.exception("Không gọi được endpoint %s", ep.name)
        raise HTTPException(502, f"Không kết nối được endpoint {ep.name}: {type(ex).__name__}") from ex
    if resp.status_code >= 400:
        log.error("Endpoint %s trả %s: %s", ep.name, resp.status_code, resp.text[:500])
        raise HTTPException(502, f"Endpoint {ep.name} trả lỗi HTTP {resp.status_code}")
    data = resp.json()
    # Scoring script trả dict nhưng một số phiên bản inference server bọc thêm 1 lớp chuỗi JSON
    return json.loads(data) if isinstance(data, str) else data


# ---------- Phân loại ý định ----------

class ClassifyRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=20)


def _mock_label(text: str) -> tuple[str, float]:
    t = text.lower()
    rules = [  # (nhãn, từ khóa) – chỉ để demo giao diện khi chưa có endpoint
        ("fraud", ("fraud", "not recognis", "don't recognis", "didn't make", "without permission",
                   "gian lận", "không phải tôi", "lạ")),
        ("card_block", ("lost", "stolen", "block", "freeze", "swallow", "mất thẻ", "khóa thẻ", "khoá thẻ")),
        ("balance_inquiry", ("balance", "top up", "top-up", "refund", "số dư", "hoàn tiền")),
        ("complaint", ("complain", "failed", "declined", "charged twice", "fee", "khiếu nại", "phí", "lỗi")),
    ]
    for label, words in rules:
        hits = sum(w in t for w in words)
        if hits:
            return label, min(0.6 + 0.15 * hits, 0.97)
    return "other", 0.42


def _normalize_predictions(texts: list[str], raw: dict) -> list[dict]:
    if "error" in raw:
        raise HTTPException(502, f"Model báo lỗi: {raw['error']}")
    preds = raw.get("predictions")
    if not isinstance(preds, list) or len(preds) != len(texts):
        raise HTTPException(502, "Phản hồi của endpoint phân loại không đúng định dạng")
    return [
        {"text": text, "label": p["label"], "label_vi": LABELS_VI.get(p["label"], p["label"]),
         "confidence": float(p["confidence"])}
        for text, p in zip(texts, preds)
    ]


# ---------- Chatbot RAG ----------

class Turn(BaseModel):
    question: str
    answer: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    chat_history: list[Turn] = Field(default_factory=list, max_length=20)


SOURCE_RE = re.compile(r"\[Nguồn (\d+): ([^\]]+)\]\n")


def parse_sources(context: str) -> list[dict]:
    """Tách chuỗi context của node lookup ("[Nguồn i: tiêu đề]\\nnội dung") thành danh sách nguồn."""
    parts = SOURCE_RE.split(context or "")
    # split -> ["", "1", "tiêu đề", "nội dung", "2", ...]
    return [
        {"id": int(parts[i]), "title": parts[i + 1].strip(), "excerpt": parts[i + 2].strip()}
        for i in range(1, len(parts) - 2, 3)
    ]


def to_flow_history(history: list[Turn]) -> list[dict]:
    """Định dạng chat_history mà Prompt Flow chat flow yêu cầu."""
    return [{"inputs": {"question": t.question}, "outputs": {"answer": t.answer}} for t in history]


MOCK_BLOCK_WORDS = ("giết", "tự tử", "đốt", "thẻ giả", "bỏ qua mọi hướng dẫn", "system prompt")


def _mock_chat(question: str) -> dict:
    if any(w in question.lower() for w in MOCK_BLOCK_WORDS):
        return {"answer": "(MOCK) Xin lỗi, tôi không thể hỗ trợ yêu cầu này.",
                "context": "", "safety": {"safe": False, "llm_guard": {"category": "mock"}}}
    context = ("[Nguồn 1: Điều khoản và Điều kiện sử dụng thẻ tín dụng]\n"
               "(MOCK) Chủ thẻ phải thông báo ngay cho Ngân hàng khi thẻ bị mất, thất lạc hoặc bị lộ thông tin...")
    return {"answer": "(MOCK) Đây là câu trả lời giả lập. Khi cấu hình CHAT_URL, câu trả lời sẽ lấy từ "
                      "chatbot RAG trên Azure và trích dẫn Điều khoản tương ứng [Nguồn 1].",
            "context": context, "safety": {"safe": True}}


# ---------- App ----------

app = FastAPI(title="Trợ lý tín dụng – Web demo", docs_url="/api/docs", redoc_url=None)
CLASSIFIER = Endpoint.from_env("CLASSIFIER")
CHAT = Endpoint.from_env("CHAT")


def _mode(ep: Endpoint) -> str:
    return "live" if ep.live else "mock"


@app.get("/api/health")
def health():
    return {"status": "ok", "classifier": _mode(CLASSIFIER), "chat": _mode(CHAT)}


@app.post("/api/classify")
async def classify(req: ClassifyRequest):
    texts = [t.strip() for t in req.texts]
    if not all(texts) or any(len(t) > 1000 for t in texts):
        raise HTTPException(422, "Mỗi câu phải có từ 1 đến 1000 ký tự")
    start = time.perf_counter()
    if CLASSIFIER.live:
        predictions = _normalize_predictions(texts, await call_endpoint(CLASSIFIER, {"data": texts}))
    else:
        predictions = [{"text": t, "label": lbl, "label_vi": LABELS_VI[lbl], "confidence": round(conf, 4)}
                       for t in texts for lbl, conf in [_mock_label(t)]]
    return {"mode": _mode(CLASSIFIER), "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "predictions": predictions}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    start = time.perf_counter()
    if CHAT.live:
        raw = await call_endpoint(CHAT, {"question": req.question,
                                         "chat_history": to_flow_history(req.chat_history)})
    else:
        raw = _mock_chat(req.question)
    if "answer" not in raw:
        raise HTTPException(502, "Phản hồi của chatbot không có trường 'answer'")
    safety = raw.get("safety") or {}
    return {"mode": _mode(CHAT), "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "answer": raw["answer"], "blocked": safety.get("safe") is False,
            "sources": parse_sources(raw.get("context", "")), "safety": safety}


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(APP_DIR / "static" / "index.html")
