# AGENTS.md – Ghi chú cho agent/AI làm việc trong repo IS402

Repo môn **IS402 – Điện toán đám mây (UIT)**. Đồ án chính nằm ở `BTDA/`: **Bài tập 14 – Phân loại ý định & Trợ lý tín dụng (MLOps & GenAIOps trên Azure)**. `BTQT/` (bài quá trình) và `LT/` (slide lý thuyết) là tài liệu, không sửa.

> **Repo public.** Không commit key, connection string, `.env`, subscription ID, link tài liệu nội bộ của nhóm hay kết quả chứa các thông tin đó. Mọi script lấy key qua `az` lúc chạy.

## Nhóm và cách nhận việc

| Thành viên | Vai trò | Phạm vi |
|---|---|---|
| Bảo | Data Engineer, **Owner** subscription | DataOps, hạ tầng Azure, cấp quyền RBAC |
| Vũ | ML Engineer | MLOps: Azure ML job, pipeline, model registry, endpoint phân loại (blue/green) |
| Bằng | GenAI Engineer | RAG chatbot: tài liệu, AI Search, Prompt Flow, evaluation, Content Safety |
| Hương | App / DevOps / Báo cáo | Web demo, CI/CD, gom báo cáo |

- Bảng phân công/tiến độ là Google Sheet của nhóm (link trong nhóm chat, **không** đưa vào repo). Mỗi task có ID `T01`–`T51`, cột **Phụ thuộc** và **Ảnh cần chụp cho báo cáo**.
- Chỉ bắt đầu task khi mọi task phụ thuộc đã "Hoàn thành"; làm xong cập nhật trạng thái và chụp ảnh minh chứng.
- Git: mỗi người một branch (`<tên>/<chủ đề>`, ví dụ `bang/genai`), merge vào `main` qua Pull Request. Commit bằng tài khoản GitHub của chính thành viên đó.

## Kết nối Azure

- Subscription **Azure for Students** thuộc tenant UIT (`ms.uit.edu.vn`); đăng nhập bằng `<MSSV>@ms.uit.edu.vn`. Tài nguyên dùng chung trong RG **`rg-credit-assistant`**, region **`japaneast`**.
- Mỗi thành viên có **Contributor** trên RG (+ **Storage Blob Data Contributor** trên `stcredit23520121`). Contributor **không** gán role và **không** đăng ký resource provider được → việc cần role assignment phải nhờ Bảo.
- Cài đặt (Windows):
  ```powershell
  winget install -e --id Microsoft.AzureCLI      # sau đó thoát hẳn VS Code (File > Exit) để PATH có az
  az extension add -n ml
  az login --tenant ms.uit.edu.vn
  # nếu cửa sổ đăng nhập của Windows không hiện: az config set core.enable_broker_on_windows=false
  ```
- **Azure MCP cho Claude Code** (tùy chọn, dùng phiên `az login`):
  ```powershell
  claude mcp add azure --scope user -- cmd /c npx -y @azure/mcp@3.0.0 server start
  ```
  Trên Windows cần `cmd /c`; ghim `3.0.0` vì `@latest` đang là bản beta. Kiểm tra: `claude mcp get azure` → `Connected`.

## Tài nguyên Azure

| Tài nguyên | Tên | Phụ trách | Ghi chú |
|---|---|---|---|
| Data Lake (ADLS Gen2) | `stcredit23520121` | Bảo | Container `raw`, `processed`, `rag-docs` |
| Azure ML workspace | `mlw-credit-assistant` | Bảo/Vũ | `cpu-cluster`, datastore `datalake_raw`/`datalake_processed`, data asset `banking77-processed:1` |
| Azure AI Services | `ais-credit-assistant` | Bằng | `gpt-4.1-mini` (100K TPM, guardrail `credit-assistant-guardrail`), `text-embedding-3-small` (50K TPM) |
| Azure AI Search (Free) | `srch-credit-assistant` | Bằng | Index `credit-card-policy`, indexer chạy 5 phút/lần trên `rag-docs/credit-card-policy/` |
| Foundry hub / project | `hub-credit-assistant` / `proj-credit-assistant` | Bằng | Connection `aoai_connection`, `search_connection`, `content_safety_connection`; flow `credit-assistant-rag` |

Chi tiết từng phần: [BTDA/README.md](BTDA/README.md) (DataOps/MLOps) và [BTDA/genai/README.md](BTDA/genai/README.md) (GenAIOps, runbook đầy đủ).

## Trạng thái phần GenAI (cập nhật 2026-10-25)

| Task | Trạng thái | Kết quả |
|---|---|---|
| T24 | Xong | `BTDA/genai/docs/dieu-khoan-dieu-kien-the-tin-dung.pdf` (10 trang, ngân hàng giả định) |
| T25 | Xong (đổi model) | `gpt-4.1-mini` thay GPT-4o vì quota gpt-4o = 0; embedding `text-embedding-3-small` |
| T26 | Xong | `srch-credit-assistant` (Free) |
| T27 | Xong | 30 chunk; indexer tự chạy khi PDF trong `rag-docs/credit-card-policy/` thay đổi |
| T28 | Xong | `promptflow/credit-assistant/chat.jinja2` |
| T29 | Xong | Flow `safety_check → lookup → chat_with_llm`, đã lên project Foundry |
| T30 | Xong | `eval/eval_data.jsonl` (35 câu) |
| T31 | Xong | Groundedness > 4 (quality gate đạt), xem `BTDA/genai/docs/chuong-genaiops.md` |
| T42 | Xong (làm trong flow) | 3 lớp: Content Safety + Prompt Shields, LLM guard, guardrail của deployment |
| T34 | **Chưa làm** | Cần Bảo gán role cho identity của online endpoint; endpoint tính phí theo giờ → chỉ bật gần ngày demo |
| T49 | Bản nháp | `BTDA/genai/docs/chuong-genaiops.md` |

## Lưu ý kỹ thuật

- **Model:** quota của subscription cho gpt-4o (GlobalStandard, Standard) và gpt-4.1 là 0. Muốn kiểm tra quota một model: thử deploy với capacity nhỏ (API `usages` trả rỗng khi chỉ có quyền RG).
- **Prompt Flow** chỉ chạy với hub-based project / Azure ML workspace, bị khai tử 20/04/2027.
- `az ml workspace create --kind project` (extension `ml` 2.45.2) lỗi → tạo project bằng ARM REST. `promptflow-azure 1.18.5` cần `azure-ai-ml==1.28.1`.
- Role của managed identity mới tạo cần ~10–30 phút để có hiệu lực (lỗi 401 khi `pfazure flow create`).
- Chi phí: AI Search Free, model trả theo token; **online endpoint (T32/T34) tính theo giờ VM** – tạo gần ngày demo và xóa sau demo (T51). Budget alert do Hương quản lý (T15).
