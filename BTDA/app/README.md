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
# Hoặc đo qua web demo: web trên Azure (T40) so với web chạy local – cùng body latency_request_web.json
.venv\Scripts\python latency_test.py `
    --target web-azure=https://app-credit-assistant-is402.azurewebsites.net/api/classify `
    --target web-local=http://localhost:8000/api/classify `
    --request-file latency_request_web.json -n 50
```

Đo độ trễ mạng thuần tới server (không gọi model) bằng `--method GET`, ví dụ so sánh server ở 2 region:

```powershell
.venv\Scripts\python latency_test.py --method GET `
    --target japaneast=https://app-credit-assistant-is402.azurewebsites.net/api/health `
    --target <region-2>=https://<web-app-region-2>.azurewebsites.net/api/health -n 100
```

Kết quả đo ngày 23/11/2026 (server japaneast so với koreacentral, endpoint model ở japaneast) đã ghi trong báo cáo, mục 3.4. Bản web ở koreacentral được xóa ngay sau khi đo.

Script dừng và báo lỗi nếu endpoint chưa có deployment nhận traffic. Endpoint thứ hai ở region khác chưa có. Nhóm cần thống nhất với Vũ trước khi tạo, vì nó tốn tiền theo giờ và quota vCPU của mỗi region khác nhau.

## Deploy lên Azure App Service (T40)

**URL:** https://app-credit-assistant-is402.azurewebsites.net

| Tài nguyên | Tên | Cấu hình |
|---|---|---|
| App Service plan | `plan-credit-assistant` | Linux, **F1 (Free, 0 USD)**, `japaneast` |
| Web app | `app-credit-assistant-is402` | Python 3.12, gunicorn + uvicorn worker (2 worker), chỉ HTTPS (TLS ≥ 1.2), tắt FTP/FTPS |

Deploy hoặc cập nhật (cần `az login`, quyền Contributor trên RG). Chạy lại nhiều lần được, lần sau chỉ cập nhật code và cấu hình:

```powershell
cd BTDA\app
.\deploy_webapp.ps1
```

Script làm 3 việc:
1. Tạo plan và web app nếu chưa có.
2. Đặt lệnh khởi động và bật HTTPS-only. Chép các dòng `CLASSIFIER_*` / `CHAT_*` có giá trị từ `.env` vào **App settings** (biến môi trường trên Azure). Script không in giá trị ra màn hình.
3. Đóng gói **chỉ** `main.py`, `requirements.txt` và `static/` (không có `.env`, `.venv`, test), rồi zip deploy. Azure tự cài thư viện từ `requirements.txt`.

Khi đổi URL hoặc key (ví dụ Bằng gửi URL chatbot, hoặc Vũ tạo lại key), sửa `.env` rồi chạy lại script.

**Giới hạn của gói F1:** 60 phút CPU mỗi ngày. Web ngủ sau khoảng 20 phút không có truy cập, nên lần mở đầu tiên sau đó mất khoảng 30–60 giây để khởi động. Trước buổi demo nên mở web trước vài phút. Endpoint đang tắt thì web vẫn chạy, chỉ báo lỗi ở trang tương ứng.

Xem log: `az webapp log tail -n app-credit-assistant-is402 -g rg-credit-assistant`. Xóa sau demo (T51): `az webapp delete -n app-credit-assistant-is402 -g rg-credit-assistant` và `az appservice plan delete -n plan-credit-assistant -g rg-credit-assistant --yes`.

Ảnh minh chứng T40: web chạy trên URL Azure và trả kết quả thật, kèm trang Overview của web app trên Portal (URL, Status Running, plan F1).

## Việc tiếp theo

- **T37:** đã nối endpoint phân loại. Còn chatbot, điền `CHAT_URL` và `CHAT_KEY` khi T34 xong, rồi chạy lại `deploy_webapp.ps1`.
- **T39:** thêm nút Thích/Không thích. Backend sẽ có `POST /api/feedback` ghi vào Cosmos DB `cosmos-credit-23520121` (T38 của Bảo).
