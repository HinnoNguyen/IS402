"""Test backend web demo (chạy offline, không cần Azure):  pytest -q   (trong BTDA/app)"""
import os

os.environ["MOCK_MODE"] = "true"  # không bao giờ gọi Azure thật trong test

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)


def test_index_and_health():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok", "classifier": "mock", "chat": "mock",
                                                "feedback": "mock"}


def test_classify_mock():
    texts = ["I lost my card, please block it", "Someone used my card without permission", "hello"]
    res = client.post("/api/classify", json={"texts": texts}).json()
    assert res["mode"] == "mock"
    assert [p["label"] for p in res["predictions"]] == ["card_block", "fraud", "other"]
    assert all(p["label_vi"] == main.LABELS_VI[p["label"]] for p in res["predictions"])


@pytest.mark.parametrize("body", [{"texts": []}, {"texts": ["  "]}, {"texts": ["x" * 1001]}, {}])
def test_classify_rejects_bad_input(body):
    assert client.post("/api/classify", json=body).status_code == 422


def test_chat_mock_answer_and_block():
    ok = client.post("/api/chat", json={"question": "Phí rút tiền mặt là bao nhiêu?"}).json()
    assert not ok["blocked"] and ok["sources"][0]["id"] == 1
    bad = client.post("/api/chat", json={"question": "Nợ nhiều quá tôi muốn tự tử"}).json()
    assert bad["blocked"] and bad["sources"] == []


def test_normalize_live_classifier_response():
    raw = {"predictions": [{"label": "fraud", "confidence": 0.91}]}
    assert main._normalize_predictions(["x"], raw) == [
        {"text": "x", "label": "fraud", "label_vi": "Gian lận", "confidence": 0.91}]
    with pytest.raises(HTTPException):
        main._normalize_predictions(["x"], {"error": "Input must contain a 'text' column"})
    with pytest.raises(HTTPException):
        main._normalize_predictions(["x", "y"], raw)


def test_parse_sources_matches_lookup_format():
    # cùng định dạng với genai/promptflow/credit-assistant/lookup.py
    context = "[Nguồn 1: dieu-khoan.pdf]\nĐiều 6.5 Phí rút tiền mặt...\n\n[Nguồn 2: dieu-khoan.pdf]\nĐiều 8.3 ..."
    sources = main.parse_sources(context)
    assert [s["id"] for s in sources] == [1, 2]
    assert sources[0]["excerpt"] == "Điều 6.5 Phí rút tiền mặt..."
    assert main.parse_sources("") == []


def test_flow_history_format():
    turns = [main.Turn(question="q1", answer="a1")]
    assert main.to_flow_history(turns) == [{"inputs": {"question": "q1"}, "outputs": {"answer": "a1"}}]


def test_live_chat_uses_endpoint(monkeypatch):
    captured = {}

    async def fake_call(ep, payload):
        captured.update(payload)
        return {"answer": "Theo Điều 6.5 ...", "context": "[Nguồn 1: t]\nc", "safety": {"safe": True}}

    monkeypatch.setattr(main, "CHAT", main.Endpoint("chat", "https://example.invalid/score", "k", ""))
    monkeypatch.setattr(main.Endpoint, "live", property(lambda self: bool(self.url)))
    monkeypatch.setattr(main, "call_endpoint", fake_call)
    res = client.post("/api/chat", json={"question": "Q2", "chat_history": [{"question": "Q1", "answer": "A1"}]})
    assert res.json()["mode"] == "live" and res.json()["sources"] == [{"id": 1, "title": "t", "excerpt": "c"}]
    assert captured == {"question": "Q2", "chat_history": [{"inputs": {"question": "Q1"}, "outputs": {"answer": "A1"}}]}


# ---------- Phản hồi Thích/Không thích (T39) ----------

def _feedback(**kw):
    body = {"id": "11111111-aaaa", "session_id": "22222222-bbbb", "kind": "chat", "rating": "down",
            "question": "Phí rút tiền mặt?", "answer": "4% số tiền rút", "sources": ["Nguồn 1: dieu-khoan.pdf"],
            "model_mode": "live", "comment": "thiếu mức tối thiểu"}
    return {**body, **kw}


def test_feedback_saved_and_overwritten_by_id(monkeypatch):
    monkeypatch.setattr(main, "FEEDBACK", main.FeedbackStore())
    monkeypatch.setattr(main, "FEEDBACK_LIMIT", main.RateLimiter(100, 60))
    res = client.post("/api/feedback", json=_feedback())
    assert res.status_code == 200 and res.json() == {"mode": "mock", "id": "11111111-aaaa", "rating": "down"}
    doc = main.FEEDBACK.memory["11111111-aaaa"]
    assert doc["session_id"] == "22222222-bbbb" and doc["rating"] == "down" and doc["created_at"].endswith("+00:00")
    client.post("/api/feedback", json=_feedback(rating="up", comment=""))  # đổi ý -> ghi đè, không thêm bản ghi
    assert len(main.FEEDBACK.memory) == 1 and main.FEEDBACK.memory["11111111-aaaa"]["rating"] == "up"


@pytest.mark.parametrize("bad", [{"rating": "like"}, {"session_id": "x"}, {"id": "a b c d e f g h"},
                                 {"question": ""}, {"kind": "other"}, {"comment": "x" * 501},
                                 {"sources": ["s"] * 11}, {"sources": ["s" * 401]}, {"confidence": 1.5}])
def test_feedback_rejects_bad_input(bad):
    assert client.post("/api/feedback", json=_feedback(**bad)).status_code == 422


def test_feedback_rate_limited(monkeypatch):
    monkeypatch.setattr(main, "FEEDBACK", main.FeedbackStore())
    monkeypatch.setattr(main, "FEEDBACK_LIMIT", main.RateLimiter(2, 60))
    codes = [client.post("/api/feedback", json=_feedback(id=f"id-000000{i}")).status_code for i in range(3)]
    assert codes == [200, 200, 429]


def test_feedback_live_writes_to_cosmos(monkeypatch):
    saved = []

    class FakeContainer:
        def upsert_item(self, doc):
            saved.append(doc)

    store = main.FeedbackStore()
    store.endpoint = "https://example.invalid:443/"
    store._container = FakeContainer()
    monkeypatch.delenv("MOCK_MODE")
    monkeypatch.setattr(main, "FEEDBACK", store)
    monkeypatch.setattr(main, "FEEDBACK_LIMIT", main.RateLimiter(100, 60))
    res = client.post("/api/feedback", json=_feedback(kind="classify", answer="fraud", confidence=0.94, sources=[]))
    assert res.json()["mode"] == "live" and saved[0]["kind"] == "classify" and saved[0]["confidence"] == 0.94


def test_feedback_cosmos_error_is_502(monkeypatch):
    store = main.FeedbackStore()
    store.endpoint = "https://example.invalid:443/"

    class Broken:
        def upsert_item(self, doc):
            raise PermissionError("Forbidden")

    store._container = Broken()
    monkeypatch.delenv("MOCK_MODE")
    monkeypatch.setattr(main, "FEEDBACK", store)
    monkeypatch.setattr(main, "FEEDBACK_LIMIT", main.RateLimiter(100, 60))
    res = client.post("/api/feedback", json=_feedback())
    assert res.status_code == 502 and "Cosmos DB" in res.json()["detail"]
