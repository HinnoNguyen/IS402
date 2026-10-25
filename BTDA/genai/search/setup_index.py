"""T27 – RAG ingestion pipeline on Azure AI Search (integrated vectorization).

Blob (ADLS raw docs) -> indexer cracks PDF -> SplitSkill (chunks) -> AzureOpenAIEmbeddingSkill
-> index `credit-card-policy` (one search document per chunk, vector + text) with a schedule,
so any new/changed PDF dropped into rag-docs/credit-card-policy/ is indexed automatically.

Keys are read at run time through the Azure CLI (`az login` first); nothing secret is stored in the repo.

    python search/setup_index.py                 # create/update everything and run the indexer
    python search/setup_index.py --reset         # delete and recreate (needed after schema changes)
    python search/setup_index.py --query "Phí rút tiền mặt là bao nhiêu?"
"""
import argparse
import json
import shutil
import subprocess
import sys
import time

import requests

RG = "rg-credit-assistant"
SEARCH = "srch-credit-assistant"
STORAGE = "stcredit23520121"
CONTAINER, FOLDER = "rag-docs", "credit-card-policy"
AOAI = "ais-credit-assistant"
EMBED_DEPLOYMENT, EMBED_MODEL, EMBED_DIM = "text-embedding-3-small", "text-embedding-3-small", 1536
INDEX = "credit-card-policy"
DATASOURCE, SKILLSET, INDEXER = f"{INDEX}-ds", f"{INDEX}-ss", f"{INDEX}-indexer"
API = "2024-07-01"


def az(*args):
    exe = shutil.which("az") or shutil.which("az.cmd")
    return subprocess.run([exe, *args, "-o", "tsv"], check=True, capture_output=True, text=True).stdout.strip()


def definitions(storage_key, aoai_key):
    aoai_uri = f"https://{AOAI}.openai.azure.com"
    datasource = {
        "name": DATASOURCE,
        "type": "adlsgen2",
        "credentials": {"connectionString": f"DefaultEndpointsProtocol=https;AccountName={STORAGE};"
                                            f"AccountKey={storage_key};EndpointSuffix=core.windows.net"},
        "container": {"name": CONTAINER, "query": FOLDER},
    }
    index = {
        "name": INDEX,
        "fields": [
            {"name": "chunk_id", "type": "Edm.String", "key": True, "searchable": True, "analyzer": "keyword"},
            {"name": "parent_id", "type": "Edm.String", "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True, "filterable": True},
            {"name": "chunk", "type": "Edm.String", "searchable": True, "analyzer": "vi.microsoft"},
            {"name": "text_vector", "type": "Collection(Edm.Single)", "searchable": True, "retrievable": False,
             "dimensions": EMBED_DIM, "vectorSearchProfile": "vector-profile"},
        ],
        "vectorSearch": {
            "algorithms": [{"name": "hnsw", "kind": "hnsw", "hnswParameters": {"metric": "cosine"}}],
            "profiles": [{"name": "vector-profile", "algorithm": "hnsw", "vectorizer": "aoai-vectorizer"}],
            # query-time vectorization: callers send plain text, the index embeds it with the same model
            "vectorizers": [{"name": "aoai-vectorizer", "kind": "azureOpenAI", "azureOpenAIParameters": {
                "resourceUri": aoai_uri, "deploymentId": EMBED_DEPLOYMENT, "modelName": EMBED_MODEL, "apiKey": aoai_key}}],
        },
        "semantic": {"configurations": [{"name": "semantic-config", "prioritizedFields": {
            "titleField": {"fieldName": "title"}, "prioritizedContentFields": [{"fieldName": "chunk"}]}}]},
    }
    skillset = {
        "name": SKILLSET,
        "description": "Chunk PDF text and embed each chunk with Azure OpenAI",
        "skills": [
            {"@odata.type": "#Microsoft.Skills.Text.SplitSkill", "name": "split", "context": "/document",
             "textSplitMode": "pages", "maximumPageLength": 1200, "pageOverlapLength": 200,
             "inputs": [{"name": "text", "source": "/document/content"}],
             "outputs": [{"name": "textItems", "targetName": "pages"}]},
            {"@odata.type": "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill", "name": "embed",
             "context": "/document/pages/*", "resourceUri": aoai_uri, "deploymentId": EMBED_DEPLOYMENT,
             "modelName": EMBED_MODEL, "dimensions": EMBED_DIM, "apiKey": aoai_key,
             "inputs": [{"name": "text", "source": "/document/pages/*"}],
             "outputs": [{"name": "embedding", "targetName": "text_vector"}]},
        ],
        # one index document per chunk; the parent (whole PDF) is not indexed
        "indexProjections": {
            "selectors": [{"targetIndexName": INDEX, "parentKeyFieldName": "parent_id", "sourceContext": "/document/pages/*",
                           "mappings": [{"name": "chunk", "source": "/document/pages/*"},
                                        {"name": "text_vector", "source": "/document/pages/*/text_vector"},
                                        {"name": "title", "source": "/document/metadata_storage_name"}]}],
            "parameters": {"projectionMode": "skipIndexingParentDocuments"},
        },
    }
    indexer = {
        "name": INDEXER,
        "dataSourceName": DATASOURCE,
        "targetIndexName": INDEX,
        "skillsetName": SKILLSET,
        "schedule": {"interval": "PT5M"},  # picks up new/changed PDFs every 5 minutes
        "parameters": {"configuration": {"dataToExtract": "contentAndMetadata", "parsingMode": "default",
                                         "indexedFileNameExtensions": ".pdf,.docx,.txt,.md"}},
    }
    return datasource, index, skillset, indexer


class Search:
    def __init__(self, key):
        self.base = f"https://{SEARCH}.search.windows.net"
        self.h = {"api-key": key, "Content-Type": "application/json"}

    def call(self, method, path, body=None, ok=(200, 201, 202, 204)):
        r = requests.request(method, f"{self.base}/{path}?api-version={API}", headers=self.h,
                             data=json.dumps(body) if body is not None else None, timeout=60)
        if r.status_code not in ok:
            sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:800]}")
        if "json" in r.headers.get("Content-Type", ""):
            return r.json()
        return r.text.lstrip("﻿").strip()


def wait_for_indexer(s, timeout=600):
    time.sleep(5)  # let the run we just triggered show up as inProgress
    start = time.time()
    while time.time() - start < timeout:
        last = s.call("GET", f"indexers/{INDEXER}/status").get("lastResult") or {}
        if last.get("status") not in (None, "inProgress"):
            return last
        time.sleep(10)
    sys.exit("indexer did not finish in time")


def query(s, text, top=3):
    body = {"search": text, "top": top, "select": "title,chunk",
            "vectorQueries": [{"kind": "text", "text": text, "fields": "text_vector", "k": 10}],
            "queryType": "semantic", "semanticConfiguration": "semantic-config"}
    for hit in s.call("POST", f"indexes/{INDEX}/docs/search", body)["value"]:
        print(f"[{hit.get('@search.rerankerScore', 0):.2f}] {hit['title']}: {hit['chunk'][:220].strip()!r}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true", help="delete indexer/skillset/index/datasource first")
    p.add_argument("--query", help="only run a test hybrid + semantic query")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    s = Search(az("search", "admin-key", "show", "--service-name", SEARCH, "-g", RG, "--query", "primaryKey"))
    if args.query:
        return query(s, args.query)

    if args.reset:
        for path in (f"indexers/{INDEXER}", f"skillsets/{SKILLSET}", f"indexes/{INDEX}", f"datasources/{DATASOURCE}"):
            s.call("DELETE", path, ok=(204, 404))
    storage_key = az("storage", "account", "keys", "list", "-n", STORAGE, "-g", RG, "--query", "[0].value")
    aoai_key = az("cognitiveservices", "account", "keys", "list", "-n", AOAI, "-g", RG, "--query", "key1")
    datasource, index, skillset, indexer = definitions(storage_key, aoai_key)
    for kind, body in (("datasources", datasource), ("indexes", index), ("skillsets", skillset), ("indexers", indexer)):
        s.call("PUT", f"{kind}/{body['name']}", body)
        print(f"PUT {kind}/{body['name']} ok")

    s.call("POST", f"indexers/{INDEXER}/run", ok=(202, 409))  # 409 = already running (just created)
    last = wait_for_indexer(s)
    print(f"indexer: {last['status']} | processed {last.get('itemsProcessed')} | failed {last.get('itemsFailed')}"
          f" | errors {[e.get('errorMessage') for e in last.get('errors', [])][:2]}")
    count = s.call("GET", f"indexes/{INDEX}/docs/$count")
    print(f"chunks in index '{INDEX}': {count}")


if __name__ == "__main__":
    main()
