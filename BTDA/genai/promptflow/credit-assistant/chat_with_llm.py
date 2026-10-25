"""Render chat.jinja2 (system rules + retrieved context + history) and ask the chat model for a grounded answer."""
import re
from pathlib import Path

from jinja2 import Template
from openai import AzureOpenAI, BadRequestError
from promptflow.connections import AzureOpenAIConnection
from promptflow.core import tool

TEMPLATE = Template(Path(__file__).with_name("chat.jinja2").read_text(encoding="utf-8"))
ROLE_LINE = re.compile(r"^#?\s*(system|user|assistant)\s*:\s*$", re.IGNORECASE | re.MULTILINE)
REFUSAL = ("Xin lỗi Quý khách, em không thể hỗ trợ yêu cầu này vì nội dung không phù hợp với chính sách an toàn "
           "của Ngân hàng. Quý khách vui lòng đặt câu hỏi về thẻ tín dụng hoặc liên hệ Tổng đài 24/7 1900 0402.")
SELF_HARM_SUPPORT = ("Em rất tiếc khi biết Quý khách đang trải qua giai đoạn khó khăn. Sự an toàn của Quý khách là quan "
                     "trọng nhất: nếu Quý khách đang gặp nguy hiểm, xin hãy gọi ngay cấp cứu 115 hoặc liên hệ người thân. "
                     "Về khoản nợ thẻ, Ngân hàng có thể tư vấn phương án chuyển dư nợ sang trả góp (Điều 14.5) – Quý khách "
                     "vui lòng gọi Tổng đài 24/7 1900 0402 để được hỗ trợ.")


def to_messages(prompt: str) -> list:
    """Split the rendered template on '# system:' / '# user:' / '# assistant:' lines (Prompt Flow chat format)."""
    parts = ROLE_LINE.split(prompt)
    return [{"role": role.lower(), "content": text.strip()}
            for role, text in zip(parts[1::2], parts[2::2]) if text.strip()]


@tool
def chat_with_llm(aoai: AzureOpenAIConnection, deployment_name: str, question: str, contexts: str,
                  chat_history: list, safety: dict, temperature: float = 0.2, max_tokens: int = 800) -> str:
    if not safety.get("safe", True):
        return SELF_HARM_SUPPORT if safety.get("self_harm") else REFUSAL

    messages = to_messages(TEMPLATE.render(question=question, contexts=contexts, chat_history=chat_history or []))
    client = AzureOpenAI(azure_endpoint=aoai.api_base, api_key=aoai.api_key, api_version=aoai.api_version)
    try:
        resp = client.chat.completions.create(model=deployment_name, messages=messages,
                                              temperature=temperature, max_tokens=max_tokens)
    except BadRequestError as err:  # second safety layer: the deployment's Azure OpenAI content filter (prompt)
        if "content_filter" in str(err):
            return REFUSAL
        raise
    choice = resp.choices[0]
    if choice.finish_reason == "content_filter":  # the filter cut the completion itself
        return REFUSAL
    return choice.message.content
