# Bài tập 14 – Phân loại ý định & Trợ lý tín dụng (MLOps & GenAIOps)

## Cấu trúc thư mục

| Thư mục / file | Nội dung |
|---|---|
| `banking_data/` | Dữ liệu gốc Banking77 (train 10.003 câu, test 3.080 câu, 77 intent) |
| `config/intent_mapping.json` | Gom 77 intent → 5 nhóm: Khiếu nại, Hỏi đáp số dư, Khóa thẻ, Gian lận, Khác (version `v2`) |
| `src/data_prep.py` | ETL: kiểm tra chất lượng dữ liệu, làm sạch, gán nhãn, xuất `quality_report.json` |
| `src/train.py` | Train 3 model (LogReg, LinearSVC, XGBoost), log MLflow, quality gate F1 > 0.85 |
| `src/intent_model.py` | Đóng gói model dạng MLflow pyfunc (trả về nhãn + độ tin cậy) |
| `azureml/` | YAML cho Azure ML: datastore, compute cluster, component, pipeline ETL, schedule |
| `genai/` | GenAIOps: tài liệu điều khoản thẻ (RAG), AI Search index, Prompt Flow chatbot, evaluation, Content Safety – xem [genai/README.md](genai/README.md) |
| `app/` | Web demo (FastAPI + HTML/JS): trang phân loại và trang chat gọi 2 endpoint, script đo độ trễ theo region – xem [app/README.md](app/README.md) |
| `../.github/workflows/` | CI/CD bằng GitHub Actions: `ci.yml` (train + quality gate, test web), `cd.yml` (train trên Azure ML → đăng ký model → deploy blue/green) – xem mục CI/CD bên dưới |

## Tài nguyên Azure (region `japaneast`)

| Tài nguyên | Tên |
|---|---|
| Resource Group | `rg-credit-assistant` |
| Data Lake (ADLS Gen2) | `stcredit23520121` – container `raw`, `processed`, `rag-docs` |
| Azure ML Workspace | `mlw-credit-assistant` |
| Compute cluster | `cpu-cluster` (Standard_F4s_v2, 0–2 node) |
| Datastore | `datalake_raw`, `datalake_processed` |
| Data Asset | `azureml:banking77-processed:1` |
| Azure AI Services (GenAI) | `ais-credit-assistant` – deployment `gpt-4.1-mini`, `text-embedding-3-small` |
| Azure AI Search (Free) | `srch-credit-assistant` – index `credit-card-policy` |
| Foundry hub / project | `hub-credit-assistant` / `proj-credit-assistant` – flow `credit-assistant-rag` |
| App Service (web demo) | `app-credit-assistant-is402` trên plan `plan-credit-assistant` (Linux F1, miễn phí) – https://app-credit-assistant-is402.azurewebsites.net |

## Chạy trên máy

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python src\data_prep.py
.venv\Scripts\python src\train.py
.venv\Scripts\mlflow ui --backend-store-uri sqlite:///mlflow.db
```

Kết quả hiện tại (mapping v2): LinearSVC F1 macro = 0.934.

## Chạy pipeline ETL trên Azure ML

```powershell
az ml job create --file azureml/pipelines/etl_pipeline.yml --resource-group rg-credit-assistant --workspace-name mlw-credit-assistant --web
```

Pipeline đọc partition `ingest_date` mới nhất trong `raw/banking77/`, ghi kết quả vào `processed/banking77/<job-id>/`.

## Dữ liệu lớn CFPB – so sánh xử lý laptop và cloud (TC 1.3)

Bộ [CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/) (khiếu nại thật của khách hàng tài chính Mỹ, 5,87 GB CSV, ~19,3 triệu dòng) không dùng để train. Nhóm dùng nó để phân tích khiếu nại thẻ tín dụng và làm khối lượng xử lý lớn khi so sánh hiệu năng.

| Bước | Lệnh | Kết quả |
|---|---|---|
| Nạp vào Data Lake (chạy trong Azure) | `az ml job create -f azureml/jobs/cfpb_ingest.yml -g rg-credit-assistant -w mlw-credit-assistant` | `raw/cfpb/ingest_date=YYYY-MM-DD/complaints.csv` + `manifest.json` |
| Xử lý trên cloud | `az ml job create -f azureml/jobs/cfpb_profile.yml -g rg-credit-assistant -w mlw-credit-assistant` | `processed/cfpb/<job-id>/` |
| Xử lý trên laptop | tải zip về `data/cfpb/` (đã gitignore), giải nén, rồi `python src/cfpb_profile.py --input data/cfpb --label laptop` | `outputs/cfpb/` |

`cfpb_profile.py` đọc theo từng phần 500.000 dòng, lọc khiếu nại thẻ tín dụng, thống kê theo năm/loại vấn đề và ghi thời gian chạy, RAM tối đa, tốc độ vào `perf_<label>.json`.

## CI/CD – GitHub Actions (T45)

| Workflow | Khi nào chạy | Làm gì | Cần Azure? |
|---|---|---|---|
| `ci.yml` | Mọi Pull Request và push vào `main` có thay đổi trong `BTDA/` | Chạy `data_prep.py` → `train.py` (3 model), **fail nếu F1 macro ≤ 0.85**, tóm tắt metrics, lưu confusion matrix làm artifact; test backend web | Không |
| `cd.yml` | Push vào `main` có thay đổi `src/`, `config/`, `azureml/pipelines\|components\|deployment/`, hoặc bấm **Run workflow** | Chạy `azureml/pipelines/mlops_pipeline.yml` → đăng ký model tốt nhất (`credit-intent-model`, tag F1/run_id/git_sha) → deploy vào deployment đang rảnh (blue↔green) → smoke test → traffic 10% → 100% → xóa deployment cũ. Lỗi thì tự rollback | Có |

**Tiết kiệm credit:** ở chế độ `auto` (mặc định), `cd.yml` chỉ deploy khi endpoint đang có deployment nhận traffic, tức là nhóm đang bật endpoint để demo. Nếu endpoint đang tắt, workflow chỉ train và đăng ký model. Muốn deploy dù endpoint đang tắt: Actions → *CD – train & deploy intent model* → Run workflow → `deploy = force`.

**Cài đặt đăng nhập Azure (làm 1 lần):** workflow dùng OIDC với user-assigned managed identity, nên không phải lưu mật khẩu hay key trong GitHub. Chưa cài thì `cd.yml` tự bỏ qua, không báo lỗi.

1. Thành viên có quyền Contributor tạo identity và federated credential cho nhánh `main`:

   ```powershell
   az identity create -n id-github-cicd -g rg-credit-assistant -l japaneast
   az identity federated-credential create --identity-name id-github-cicd -g rg-credit-assistant -n github-main `
       --issuer https://token.actions.githubusercontent.com `
       --subject repo:HinnoNguyen/IS402:ref:refs/heads/main --audiences api://AzureADTokenExchange
   ```

2. **Bảo (Owner)** gán quyền cho identity. Contributor không gán role được:

   ```powershell
   $principal = az identity show -n id-github-cicd -g rg-credit-assistant --query principalId -o tsv
   $ws = az ml workspace show -n mlw-credit-assistant -g rg-credit-assistant --query id -o tsv
   $st = az storage account show -n stcredit23520121 -g rg-credit-assistant --query id -o tsv
   az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "AzureML Data Scientist" --scope $ws
   az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role "Storage Blob Data Reader" --scope $st
   ```

   `AzureML Data Scientist` cho phép chạy job, đăng ký model và quản lý online endpoint, nhưng không cho tạo hay xóa compute. `Storage Blob Data Reader` cần vì datastore `datalake_raw` dùng identity-based access.

3. Chủ repo vào GitHub → Settings → Secrets and variables → Actions, tạo 3 **secret**: `AZURE_CLIENT_ID` (lấy bằng `az identity show -n id-github-cicd -g rg-credit-assistant --query clientId -o tsv`), `AZURE_TENANT_ID` và `AZURE_SUBSCRIPTION_ID` (lấy bằng `az account show --query "{tenant:tenantId, sub:id}"`). Phải để ở dạng *secret*, không để ở dạng *variable*, vì repo public: secret được che thành `***` trong log.
