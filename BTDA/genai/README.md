# GenAIOps – Trợ lý tín dụng (RAG chatbot)

Phần GenAI của Bài tập 14: chatbot trả lời câu hỏi về **Điều khoản và Điều kiện sử dụng thẻ tín dụng**, chỉ dựa trên tài liệu (RAG), có kiểm soát an toàn nội dung và quality gate **Groundedness > 4**.

```text
Câu hỏi ─► safety_check ─► lookup ─────────────► chat_with_llm ─► Câu trả lời (có trích dẫn Điều khoản)
           (Content Safety   (AI Search: hybrid     (chat.jinja2 +
            + Prompt Shields  vector + semantic,     gpt-4.1-mini,
            + LLM guard)      index credit-card-     guardrail)
                              policy)
PDF ─► ADLS rag-docs/credit-card-policy/ ─► indexer (5 phút/lần) ─► chunk 1200 ký tự ─► text-embedding-3-small ─► index
```

## Cấu trúc

| Đường dẫn | Nội dung | Task |
|---|---|---|
| `docs/dieu-khoan-dieu-kien-the-tin-dung.docx/.pdf` | Tài liệu giả lập 10 trang (ngân hàng giả định MayXanh Bank) – nguồn tri thức RAG | T24 |
| `azure/rai_policy.json`, `azure/deployment_gpt41mini.json` | Guardrail (content filter) tùy chỉnh và cấu hình deployment chat | T25, T42 |
| `search/setup_index.py` | Data source + index + skillset (chunk, embedding) + indexer có lịch chạy | T27 |
| `promptflow/credit-assistant/` | Prompt Flow: `flow.dag.yaml`, `chat.jinja2` (system message), các node Python | T28, T29, T42 |
| `promptflow/setup_connections.py` | Tạo 3 connection (local `pf` + project Foundry) | T29 |
| `promptflow/smoke_test.py` | Kiểm tra nhanh: câu hợp lệ được trả lời, câu độc hại bị chặn | T29, T42 |
| `eval/eval_data.jsonl` | 35 câu hỏi có đáp án chuẩn, gắn intent (5 nhóm của mô hình phân loại) | T30 |
| `eval/run_eval.py` | Chạy flow trên bộ test + chấm Groundedness/Relevance/Coherence/Similarity, log lên Foundry | T31 |

## Tài nguyên Azure (region `japaneast`, RG `rg-credit-assistant`)

| Tài nguyên | Tên | Ghi chú |
|---|---|---|
| Azure AI Services | `ais-credit-assistant` | Deployment `gpt-4.1-mini` (GlobalStandard, 100K TPM, guardrail `credit-assistant-guardrail`) và `text-embedding-3-small` (GlobalStandard, 50K TPM) |
| Azure AI Search (Free) | `srch-credit-assistant` | Index `credit-card-policy`, indexer `credit-card-policy-indexer` (PT5M), semantic ranker gói free |
| Foundry hub / project | `hub-credit-assistant` / `proj-credit-assistant` | Hub-based (bắt buộc cho Prompt Flow). Connection: `aoai_connection`, `search_connection`, `content_safety_connection`. Flow: `credit-assistant-rag` |
| Data Lake (của nhóm) | `stcredit23520121` | Container `rag-docs`, thư mục `credit-card-policy/` |

**Vì sao không dùng GPT-4o:** subscription Azure for Students có quota **0** cho gpt-4o (GlobalStandard và Standard) và gpt-4.1. `gpt-4.1-mini` (bản 2025-04-14, Azure ngừng hỗ trợ 14/04/2027) là model chat cùng thế hệ còn quota (200K TPM), không phải reasoning model nên tương thích Prompt Flow.

## Chạy lại từ đầu

Yêu cầu: Python 3.12, Azure CLI (`az login` bằng tài khoản `@ms.uit.edu.vn`, có quyền Contributor trên RG). Mọi script tự lấy key qua `az` lúc chạy – **không lưu key vào repo**.

```powershell
cd BTDA\genai
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# T27 – upload tài liệu và dựng pipeline index (thêm --reset nếu đổi schema)
az storage blob upload --account-name stcredit23520121 -c rag-docs -n credit-card-policy/dieu-khoan-dieu-kien-the-tin-dung.pdf -f docs\dieu-khoan-dieu-kien-the-tin-dung.pdf --auth-mode login --overwrite
.venv\Scripts\python search\setup_index.py
.venv\Scripts\python search\setup_index.py --query "Phí rút tiền mặt là bao nhiêu?"

# T29 – connection, chạy thử local, đưa flow lên project Foundry
.venv\Scripts\python promptflow\setup_connections.py
.venv\Scripts\python promptflow\smoke_test.py
.venv\Scripts\pf flow test --flow promptflow\credit-assistant --interactive      # chat thử trong terminal
.venv\Scripts\pfazure flow create --flow promptflow\credit-assistant --set display_name=credit-assistant-rag type=chat --subscription <sub-id> -g rg-credit-assistant -w proj-credit-assistant

# T31 – đánh giá + quality gate (kết quả trong eval\results\, không commit)
.venv\Scripts\python eval\run_eval.py
```

Guardrail và deployment chat (T25/T42) được tạo bằng ARM REST:

```powershell
$acct = "https://management.azure.com/subscriptions/<sub-id>/resourceGroups/rg-credit-assistant/providers/Microsoft.CognitiveServices/accounts/ais-credit-assistant"
az rest --method put --url "$acct/raiPolicies/credit-assistant-guardrail?api-version=2024-10-01" --body "@azure/rai_policy.json"
az rest --method put --url "$acct/deployments/gpt-4.1-mini?api-version=2024-10-01" --body "@azure/deployment_gpt41mini.json"
```

## T34 – Deploy flow thành Managed Online Endpoint (đang làm)

Endpoint dùng chung cho T35 (Data Collector), T37 (web gọi API), T42 và T46 → bật trong **một buổi hẹn với nhóm** để mọi người làm và chụp ảnh cùng lúc, xong thì xóa deployment (bước 5).

**Chi phí** (giá Linux `japaneast`, 11/2026): tính theo giờ VM của deployment, kể cả khi không có request – `Standard_DS2_v2` **0,205 USD/giờ (~4,9 USD/ngày)**. Endpoint không còn deployment thì không tốn tiền; deployment lỗi vẫn có thể bị tính nên xóa ngay. Hub chưa có Container Registry nên lần deploy đầu sẽ tự tạo một registry Basic (~0,17 USD/ngày, xóa ở T51).

**Chọn `Standard_DS2_v2`, không dùng `Standard_DS3_v2`:** online endpoint giữ thêm 20% quota cho nâng cấp, tức cần `ceil(1,2 × số instance) × số vCPU`. Quota `japaneast` của subscription: họ DSv2 **6 vCPU**, FSv2 16, tổng 20. DS3_v2 × 1 cần 8 vCPU DSv2 → lỗi `OutOfQuota`; DS2_v2 × 1 (2 vCPU, 7 GB RAM) cần 4 → đủ, và không chung họ FSv2 với `cpu-cluster` (F4s_v2) và endpoint phân loại (F2s_v2) của Vũ. Xem quota: `az ml compute list-usage -g rg-credit-assistant -w mlw-credit-assistant -o table`.

1. Foundry (classic) → `proj-credit-assistant` → Prompt flow → `credit-assistant-rag` → **Deploy**:
   - **Basic settings:** Endpoint **New** `credit-assistant-chat`, deployment `blue`, VM `Standard_DS2_v2`, instance count `1`. *Inference data collection*: hỏi Bảo (T35) trước. Bật ở đây thì input/output được ghi vào blob mặc định của hub (`workspaceblobstore`, thư mục `modelDataCollector/credit-assistant-chat/blue/`); muốn ghi thẳng vào ADLS `stcredit23520121` thì phải deploy bằng YAML có `data_collector` và Owner gán quyền ghi cho identity của endpoint.
   - **Advanced → Endpoint:** Authentication **Key-based**, Identity **System-assigned**, **giữ bật "Enforce access to connection secrets (preview)"**.
   - **Outputs & connections:** giữ mặc định (đủ `answer`, `context`, `safety`; connection kế thừa từ flow).

   Mất 15–30 phút (lần đầu phải tạo registry và build image). Theo dõi:

   ```powershell
   az ml online-deployment show -n blue -e credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --query provisioning_state -o tsv
   az ml online-deployment get-logs -n blue -e credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --lines 100
   ```

2. Kiểm tra role của identity endpoint. Với System-assigned, hệ thống tự gán **AcrPull**, **Storage Blob Data Reader** và **AzureML Metrics Writer**. Vì bật "Enforce access…" và người tạo có quyền đọc secret của connection (Contributor có), role **Azure Machine Learning Workspace Connection Secrets Reader** trên project cũng được tự gán – không cần Owner:

   ```powershell
   $principal = az ml online-endpoint show -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --query identity.principal_id -o tsv
   az role assignment list --assignee $principal --all --query "[].roleDefinitionName" -o tsv
   ```

   Nếu thiếu Connection Secrets Reader (log báo lỗi lấy connection) → nhờ **Bảo** chạy lệnh dưới, chờ 15–30 phút cho role có hiệu lực rồi deploy lại. Không cần role "AzureML Data Scientist".

   ```powershell
   $proj = az ml workspace show -n proj-credit-assistant -g rg-credit-assistant --query id -o tsv
   az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "Azure Machine Learning Workspace Connection Secrets Reader" --scope $proj
   ```

3. Gọi thử (chạy từ `BTDA\genai`) rồi chụp ảnh: deployment **Succeeded** + tab **Test** có câu trả lời (Hình 16 trong `docs/chuong-genaiops.md`); câu độc hại bị chặn khi gọi qua endpoint (T42).

   ```powershell
   az ml online-endpoint invoke -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --request-file promptflow\sample_request.json
   ```

4. Gửi URL + key cho Hương (T37) qua kênh riêng, **không commit**:

   ```powershell
   az ml online-endpoint show -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --query scoring_uri -o tsv
   az ml online-endpoint get-credentials -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --query primaryKey -o tsv
   ```

5. Xong buổi làm việc: **xóa deployment, giữ endpoint** – không tốn tiền, URL, key và role của identity giữ nguyên. Khi cần lại (ngày demo): Deploy như bước 1 nhưng chọn Endpoint **Existing** → `credit-assistant-chat`, deployment `blue`; nếu gọi báo không có deployment nhận traffic thì chạy `az ml online-endpoint update -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --traffic "blue=100"`.

   ```powershell
   az ml online-deployment delete -n blue -e credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --yes
   # T51, sau demo: xóa hẳn endpoint
   az ml online-endpoint delete -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --yes
   ```

## Kết quả

- **T27:** 1 PDF → 30 chunk. Khi upload bản PDF mới, indexer tự chạy ở lượt kế tiếp (≤ 5 phút) và cập nhật index.
- **T31** (35 câu, judge `gpt-4.1-mini`): **Groundedness 4,94** · Relevance 4,80 · Coherence 4,20 · Similarity 4,89 → quality gate đạt. Chi tiết và phân tích trong `docs/chuong-genaiops.md`.
- **T42:** smoke test 8/8 – câu hợp lệ được trả lời; đe dọa bạo lực, xúc phạm, yêu cầu làm thẻ giả, ý định tự hại và jailbreak đều bị chặn (câu tự hại nhận phản hồi hỗ trợ thay vì từ chối).

## Lưu ý / lỗi đã gặp

- **Prompt Flow chỉ dùng được với hub-based project** (không dùng được với kiểu "Foundry project" mới) và sẽ bị Microsoft khai tử ngày **20/04/2027** (thay bằng Microsoft Agent Framework).
- `az ml workspace create --kind project` (extension `ml` 2.45.2) lỗi `PublicNetworkAccessType.ENABLED` → tạo project bằng `az rest --method put` (body: `kind: Project`, `properties.hubResourceId`).
- `promptflow-azure 1.18.5` cần `azure-ai-ml==1.28.1` (bản mới hơn bỏ module mà nó import).
- Ngay sau khi tạo hub/project, `pfazure flow create` có thể báo **401** – role của managed identity cần ~10–30 phút để có hiệu lực, chờ rồi chạy lại.
- **Content Safety chấm câu tiếng Việt rất thấp** (ví dụ "Tao sẽ giết mày" → Violence 0), nên flow có thêm lớp LLM guard; ngưỡng Content Safety đặt ở mức Low (2).
- Bảng trong PDF bị trích xuất thành chữ rời rạc → các mức phí quan trọng được nhắc lại trong lời văn (Điều 6.5, 8.3) để truy xuất chính xác hơn.
- Role Contributor không gán role được: việc cần role assignment phải nhờ Bảo (Owner). Riêng identity System-assigned của online endpoint được hệ thống tự gán role (T34 bước 2).
- **Quota online endpoint** giữ thêm 20%: `Standard_DS3_v2` × 1 cần 8 vCPU họ DSv2 trong khi quota Azure for Students chỉ có 6 → dùng `Standard_DS2_v2` (T34).
