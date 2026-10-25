"""T42 – Safety gate run before RAG: Azure AI Content Safety (+ Prompt Shields) and an LLM guard for Vietnamese.

Content Safety's classifiers score Vietnamese threats very low (e.g. "Tao sẽ giết mày" -> Violence 0), so a short
LLM classification covers threats, hate and requests for illegal help that the classifiers miss.
"""
import json

import requests
from openai import AzureOpenAI, BadRequestError
from promptflow.connections import AzureContentSafetyConnection, AzureOpenAIConnection
from promptflow.core import tool

API_VERSION = "2024-09-01"
BLOCK_SEVERITY = 2  # FourSeverityLevels: 0 safe, 2 low, 4 medium, 6 high

GUARD_PROMPT = """Bạn là bộ lọc an toàn nội dung cho chatbot hỗ trợ thẻ tín dụng của một ngân hàng.
Phân loại TIN NHẮN của khách hàng và chỉ trả về JSON: {"harmful": true|false, "category": "violence|hate|sexual|self_harm|illegal|none"}.
harmful = true khi tin nhắn: đe dọa hoặc kích động bạo lực; xúc phạm, miệt thị, thù ghét; có nội dung tình dục; thể hiện ý định tự hại;
hoặc yêu cầu hướng dẫn hành vi phạm pháp (làm thẻ giả, đánh cắp thông tin thẻ, chiếm đoạt tài khoản người khác, lừa đảo, rửa tiền,
"đáo hạn thẻ", rút tiền mặt qua máy POS).
Các câu hỏi về việc bị lừa đảo, bị mất thẻ, khiếu nại, hoặc lời phàn nàn thông thường KHÔNG phải là harmful."""


def content_safety(conn: AzureContentSafetyConnection, text: str) -> tuple:
    base = conn.endpoint.rstrip("/")
    headers = {"Ocp-Apim-Subscription-Key": conn.api_key, "Content-Type": "application/json"}
    analysis = requests.post(f"{base}/contentsafety/text:analyze?api-version={API_VERSION}", headers=headers,
                             json={"text": text, "outputType": "FourSeverityLevels"}, timeout=15)
    analysis.raise_for_status()
    flagged = {c["category"]: c["severity"] for c in analysis.json()["categoriesAnalysis"]
               if c["severity"] >= BLOCK_SEVERITY}
    # Prompt Shields: jailbreak / "ignore your instructions" style attacks
    shield = requests.post(f"{base}/contentsafety/text:shieldPrompt?api-version={API_VERSION}", headers=headers,
                           json={"userPrompt": text, "documents": []}, timeout=15)
    shield.raise_for_status()
    return flagged, shield.json()["userPromptAnalysis"]["attackDetected"]


def llm_guard(conn: AzureOpenAIConnection, deployment_name: str, text: str) -> dict:
    client = AzureOpenAI(azure_endpoint=conn.api_base, api_key=conn.api_key, api_version=conn.api_version)
    try:
        resp = client.chat.completions.create(
            model=deployment_name, temperature=0, max_tokens=50, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": GUARD_PROMPT}, {"role": "user", "content": f"TIN NHẮN: {text}"}])
    except BadRequestError as err:
        if "content_filter" in str(err):  # the deployment's own content filter already refused it
            return {"harmful": True, "category": "content_filter"}
        raise
    return json.loads(resp.choices[0].message.content)


@tool
def safety_check(content_safety_conn: AzureContentSafetyConnection, aoai: AzureOpenAIConnection,
                 deployment_name: str, question: str) -> dict:
    text = question[:10000]
    flagged, attack = content_safety(content_safety_conn, text)
    guard = {"harmful": False, "category": "none"}
    if not flagged and not attack:
        guard = llm_guard(aoai, deployment_name, text)
    return {
        "safe": not flagged and not attack and not guard.get("harmful"),
        "self_harm": "SelfHarm" in flagged or guard.get("category") == "self_harm",
        "content_safety": flagged,
        "prompt_attack": attack,
        "llm_guard": guard,
    }
