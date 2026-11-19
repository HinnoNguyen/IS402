# Web demo – Trợ lý tín dụng (T36, T37, T44)

Web gồm 2 trang, cùng gọi qua backend FastAPI. Backend giữ key của endpoint, trình duyệt không thấy key:

| Trang | API backend | Gọi tới endpoint Azure | Phụ trách endpoint |
|---|---|---|---|
| **Phân loại ý định** – nhãn 5 nhóm + độ tin cậy | `POST /api/classify` `{"texts": [...]}` | `credit-intent-endpoint` (`mlw-credit-assistant`) | Vũ (T32/T33) |
| **Trợ lý tín dụng (Chat)** – trả lời theo Điều khoản, có trích dẫn, báo khi bị bộ lọc an toàn chặn | `POST /api/chat` `{"question", "chat_history"}` | `credit-assistant-chat` (`proj-credit-assistant`) | Bằng (T34) |

`GET /api/health` cho biết từng phần đang chạy `live` (gọi Azure) hay `mock`. Tài liệu API tự sinh: `/api/docs`.

```text
Trình duyệt ──fetch JSON──► FastAPI (main.py) ──HTTPS + key──► Azure ML online endpoint (phân loại)
  static/                    /api/classify                  └─► Prompt Flow online endpoint (chat RAG)
                             /api/chat
```

**Chế độ MOCK:** endpoint nào chưa điền URL thì backend trả dữ liệu giả (phân loại theo từ khóa, câu trả lời có gắn "(MOCK)"). Nhờ vậy web chạy được ngay ở local mà chưa cần bật endpoint, vốn tính tiền theo giờ. Góc trên bên phải giao diện ghi rõ từng phần đang ở **Azure** hay **MOCK**.

## Chạy ở local (T36)

```powershell
cd BTDA\app
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn main:app --reload --port 8000
# mở http://localhost:8000
```

Test (không gọi Azure): `.venv\Scripts\pip install pytest` rồi `.venv\Scripts\pytest -q`.

## Kết nối 2 endpoint thật (T37)

Chỉ làm khi endpoint đang có deployment (hỏi Vũ/Bằng trước). Lấy URL và key bằng `az`, ghi vào `BTDA\app\.env`. File này đã nằm trong `.gitignore`, **không commit và không gửi lên nhóm chat**:

```powershell
cd BTDA\app
copy .env.example .env
# Phân loại
az ml online-endpoint show -n credit-intent-endpoint -g rg-credit-assistant -w mlw-credit-assistant --query scoring_uri -o tsv
az ml online-endpoint get-credentials -n credit-intent-endpoint -g rg-credit-assistant -w mlw-credit-assistant --query primaryKey -o tsv
# Chatbot
az ml online-endpoint show -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --query scoring_uri -o tsv
az ml online-endpoint get-credentials -n credit-assistant-chat -g rg-credit-assistant -w proj-credit-assistant --query primaryKey -o tsv
```

Điền vào `CLASSIFIER_URL`, `CLASSIFIER_KEY`, `CHAT_URL`, `CHAT_KEY` rồi khởi động lại `uvicorn`. Badge trên giao diện chuyển sang **Azure**.

Ảnh minh chứng T37: giao diện có kết quả trả về, kèm DevTools (F12) → tab **Network** → request `classify` / `chat` → tab Payload và Response.

## Đo độ trễ theo region (T44)

`latency_test.py` gửi tuần tự N request tới mỗi target trên một kết nối keep-alive. Script ghi riêng thời gian request đầu tiên (gồm DNS + TLS), sau đó tính trung bình, p50, p95, min, max. Kết quả in thành bảng Markdown và lưu vào `outputs/latency/` (đã gitignore) dưới dạng `.csv`, `.md`, `.png`. File kết quả không chứa URL hay key.

```powershell
cd BTDA\app
.venv\Scripts\pip install matplotlib        # để vẽ biểu đồ
# Endpoint Azure ML: URL, key, region tự lấy bằng az
.venv\Scripts\python latency_test.py --target japaneast=azureml:credit-intent-endpoint@mlw-credit-assistant -n 50
# So sánh 2 region: thêm --target thứ hai trỏ tới endpoint ở region khác
.venv\Scripts\python latency_test.py `
    --target japaneast=azureml:credit-intent-endpoint@mlw-credit-assistant `
    --target <region-2>=azureml:<endpoint-2>@<workspace-2> -n 50
# Hoặc đo 1 URL bất kỳ, ví dụ web demo đã deploy (T40)
.venv\Scripts\python latency_test.py --target web-azure=https://<app>.azurewebsites.net/api/classify `
    --request-file <file-json> -n 50
```

Script dừng và báo lỗi nếu endpoint chưa có deployment nhận traffic. Endpoint thứ hai ở region khác chưa có. Nhóm cần thống nhất với Vũ trước khi tạo, vì nó tốn tiền theo giờ và quota vCPU của mỗi region khác nhau.

## Việc tiếp theo

- **T37:** điền `.env` khi T34 (chatbot) xong và endpoint phân loại được bật lại.
- **T39:** thêm nút Thích/Không thích. Backend sẽ có `POST /api/feedback` ghi vào Cosmos DB (T38 của Bảo).
- **T40:** deploy lên App Service (Linux, Python). Lệnh khởi động `uvicorn main:app --host 0.0.0.0 --port 8000`, URL và key đặt trong *App settings*, không đặt trong code.
