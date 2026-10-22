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

## Tài nguyên Azure (region `japaneast`)

| Tài nguyên | Tên |
|---|---|
| Resource Group | `rg-credit-assistant` |
| Data Lake (ADLS Gen2) | `stcredit23520121` – container `raw`, `processed`, `rag-docs` |
| Azure ML Workspace | `mlw-credit-assistant` |
| Compute cluster | `cpu-cluster` (Standard_F4s_v2, 0–2 node) |
| Datastore | `datalake_raw`, `datalake_processed` |
| Data Asset | `azureml:banking77-processed:1` |

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
