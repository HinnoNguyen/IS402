"""Retrieve the most relevant chunks of the credit-card T&C from Azure AI Search (hybrid + semantic ranking)."""
import requests
from promptflow.connections import CognitiveSearchConnection
from promptflow.core import tool

API_VERSION = "2024-07-01"


@tool
def lookup(search: CognitiveSearchConnection, question: str, safety: dict, index_name: str, top_k: int = 5) -> dict:
    if not safety.get("safe", True):
        return {"context": "", "documents": []}  # blocked questions never reach the index

    body = {
        "search": question,
        "top": top_k,
        "select": "title,chunk",
        # integrated vectorization: the index embeds the query text with text-embedding-3-small
        "vectorQueries": [{"kind": "text", "text": question, "fields": "text_vector", "k": 20}],
        "queryType": "semantic",
        "semanticConfiguration": "semantic-config",
    }
    resp = requests.post(f"{search.api_base.rstrip('/')}/indexes/{index_name}/docs/search?api-version={API_VERSION}",
                         headers={"api-key": search.api_key, "Content-Type": "application/json"}, json=body, timeout=30)
    resp.raise_for_status()

    documents = [{"title": hit["title"], "content": hit["chunk"], "score": hit.get("@search.rerankerScore")}
                 for hit in resp.json()["value"]]
    context = "\n\n".join(f"[Nguồn {i}: {d['title']}]\n{d['content'].strip()}" for i, d in enumerate(documents, 1))
    return {"context": context, "documents": documents}
