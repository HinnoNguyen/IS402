"""Create the three connections used by the credit-assistant flow, locally (pf) and/or in the Foundry project.

    aoai_connection            -> Azure OpenAI on ais-credit-assistant (chat + embeddings)
    search_connection          -> Azure AI Search srch-credit-assistant
    content_safety_connection  -> Azure AI Content Safety on ais-credit-assistant

Keys are read at run time through the Azure CLI (`az login` first) and never written to the repo.

    python promptflow/setup_connections.py              # local + cloud
    python promptflow/setup_connections.py --target local
"""
import argparse
import shutil
import subprocess

RG = "rg-credit-assistant"
PROJECT = "proj-credit-assistant"
AIS = "ais-credit-assistant"
SEARCH = "srch-credit-assistant"
AOAI_ENDPOINT = f"https://{AIS}.openai.azure.com/"
CONTENT_SAFETY_ENDPOINT = f"https://{AIS}.cognitiveservices.azure.com/"
SEARCH_ENDPOINT = f"https://{SEARCH}.search.windows.net"
AOAI_API_VERSION = "2024-10-21"


def az(*args):
    exe = shutil.which("az") or shutil.which("az.cmd")
    return subprocess.run([exe, *args, "-o", "tsv"], check=True, capture_output=True, text=True).stdout.strip()


def create_local(ais_key, search_key):
    from promptflow.client import PFClient
    from promptflow.entities import AzureContentSafetyConnection, AzureOpenAIConnection, CognitiveSearchConnection

    pf = PFClient()
    for conn in (
        AzureOpenAIConnection(name="aoai_connection", api_key=ais_key, api_base=AOAI_ENDPOINT, api_version=AOAI_API_VERSION),
        CognitiveSearchConnection(name="search_connection", api_key=search_key, api_base=SEARCH_ENDPOINT, api_version="2024-07-01"),
        AzureContentSafetyConnection(name="content_safety_connection", api_key=ais_key, endpoint=CONTENT_SAFETY_ENDPOINT,
                                     api_version="2024-09-01"),
    ):
        pf.connections.create_or_update(conn)
        print(f"local  connection '{conn.name}' ok")


def create_cloud(ais_key, search_key):
    from azure.ai.ml import MLClient
    from azure.ai.ml.entities import AzureAISearchConnection, AzureContentSafetyConnection, AzureOpenAIConnection
    from azure.identity import AzureCliCredential

    ml = MLClient(AzureCliCredential(), az("account", "show", "--query", "id"), RG, PROJECT)
    for conn in (
        AzureOpenAIConnection(name="aoai_connection", azure_endpoint=AOAI_ENDPOINT, api_key=ais_key,
                              api_version=AOAI_API_VERSION),
        AzureAISearchConnection(name="search_connection", endpoint=SEARCH_ENDPOINT, api_key=search_key),
        AzureContentSafetyConnection(name="content_safety_connection", endpoint=CONTENT_SAFETY_ENDPOINT, api_key=ais_key),
    ):
        ml.connections.create_or_update(conn)
        print(f"cloud  connection '{conn.name}' ok (project {PROJECT})")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=["local", "cloud", "all"], default="all")
    args = p.parse_args()
    ais_key = az("cognitiveservices", "account", "keys", "list", "-n", AIS, "-g", RG, "--query", "key1")
    # the flow only queries the index, so it gets a query key rather than the admin key
    search_key = az("search", "query-key", "list", "--service-name", SEARCH, "-g", RG, "--query", "[0].key")
    if args.target in ("local", "all"):
        create_local(ais_key, search_key)
    if args.target in ("cloud", "all"):
        create_cloud(ais_key, search_key)


if __name__ == "__main__":
    main()
