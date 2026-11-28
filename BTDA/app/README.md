# Web demo – Trợ lý tín dụng (T36, T37, T39, T40, T44)

Web gồm 2 trang, cùng gọi qua backend FastAPI. Backend giữ key của endpoint, trình duyệt không thấy key:

| Trang | API backend | Gọi tới endpoint Azure | Phụ trách endpoint |
|---|---|---|---|
| **Phân loại ý định** – nhãn 5 nhóm + độ tin cậy | `POST /api/classify` `{"texts": [...]}` | `credit-intent-endpoint` (`mlw-credit-assistant`) | Vũ (T32/T33) |
| **Trợ lý tín dụng (Chat)** – trả lời theo Điều khoản, có trích dẫn, báo khi bị bộ lọc an toàn chặn | `POST /api/chat` `{"question", "chat_history"}` | `credit-assistant-chat` (`proj-credit-assistant`) | Bằng (T34) |

Nút 👍/👎 dưới mỗi câu trả lời gọi `POST /api/feedback` → Cosmos DB (T39, xem mục bên dưới).

`GET /api/health` cho biết từng phần (`classifier`, `chat`, `feedback`) đang chạy `live` (gọi Azure) hay `mock`. Tài liệu API tự sinh: `/api/docs`.

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
2. Đặt lệnh khởi động, bật HTTPS-only và managed identity (in Object ID, xem T39). Chép các dòng `CLASSIFIER_*` / `CHAT_*` / `COSMOS_*` có giá trị từ `.env` vào **App settings** (biến môi trường trên Azure). Script không in giá trị ra màn hình.
3. Đóng gói **chỉ** `main.py`, `requirements.txt` và `static/` (không có `.env`, `.venv`, test), rồi zip deploy. Azure tự cài thư viện từ `requirements.txt`.

Khi đổi URL hoặc key (ví dụ Bằng gửi URL chatbot, hoặc Vũ tạo lại key), sửa `.env` rồi chạy lại script.

**Giới hạn của gói F1:** 60 phút CPU mỗi ngày. Web ngủ sau khoảng 20 phút không có truy cập, nên lần mở đầu tiên sau đó mất khoảng 30–60 giây để khởi động. Trước buổi demo nên mở web trước vài phút. Endpoint đang tắt thì web vẫn chạy, chỉ báo lỗi ở trang tương ứng. Nếu `az webapp deploy` báo *Deployment failed* trong khi log build ghi *Deployment successful*, đó là do F1 khởi động chậm sau khi cài thêm thư viện, quá thời gian chờ của Azure CLI. Mở `/api/health` sau 1–2 phút để kiểm tra.

Xem log: `az webapp log tail -n app-credit-assistant-is402 -g rg-credit-assistant`. Xóa sau demo (T51): `az webapp delete -n app-credit-assistant-is402 -g rg-credit-assistant` và `az appservice plan delete -n plan-credit-assistant -g rg-credit-assistant --yes`.

Ảnh minh chứng T40: web chạy trên URL Azure và trả kết quả thật, kèm trang Overview của web app trên Portal (URL, Status Running, plan F1).

## Phản hồi Thích/Không thích – human-in-the-loop (T39)

Mỗi câu trả lời của chatbot và mỗi kết quả phân loại có nút 👍/👎. Khi bấm 👎, web hiện thêm ô góp ý (không bắt buộc). Trình duyệt gửi `POST /api/feedback`, backend ghi vào Cosmos DB `cosmos-credit-23520121` → database `credit-assistant` → container `feedback` (partition key `/session_id`, Bảo tạo ở T38). Function ở T43 đọc các bản ghi `rating = "down"` để lập danh sách cần sửa prompt/RAG.

Bản ghi (một câu trả lời ứng với một bản ghi; bấm lại hoặc thêm góp ý thì ghi đè theo `id`, không sinh bản ghi mới):

| Trường | Ý nghĩa |
|---|---|
| `id`, `session_id` | Trình duyệt sinh ngẫu nhiên; `session_id` giữ nguyên trong một tab |
| `kind` | `chat` hoặc `classify` |
| `rating` | `up` / `down` |
| `question`, `answer` | Câu hỏi và câu trả lời của chatbot, hoặc câu đem phân loại và nhãn dự đoán |
| `sources` | `"Nguồn i: tiêu đề – đoạn trích"` của chatbot, để biết đoạn tài liệu nào bị đánh giá sai |
| `blocked`, `confidence`, `model_mode` | Bị bộ lọc an toàn chặn / độ tin cậy của nhãn / câu trả lời từ Azure (`live`) hay `mock` |
| `comment`, `created_at` | Góp ý khi bấm 👎; thời điểm ghi (UTC) |

**Xác thực bằng Microsoft Entra ID, không dùng key** (`DefaultAzureCredential`):

- **Local:** `az login` bằng tài khoản đã được cấp role *Cosmos DB Built-in Data Contributor* trên database `credit-assistant`, rồi điền `COSMOS_ENDPOINT` vào `.env`. Để trống thì phản hồi chỉ lưu trong bộ nhớ (MOCK).
- **App Service:** `deploy_webapp.ps1` bật system-assigned managed identity cho web app và in **Object ID**. Owner (Bảo) cấp cùng role đó cho Object ID này (đã cấp ngày 23/11/2026). Role mới cấp có thể mất vài phút mới có hiệu lực. Trong lúc đó web báo "Chưa gửi được" (lỗi 403), đợi rồi thử lại. Nếu tạo lại web app thì Object ID đổi, phải nhờ cấp lại.

Backend giới hạn 30 lần gửi mỗi phút cho mỗi IP, để tránh spam làm tốn RU của gói Free. Xem dữ liệu: Portal → `cosmos-credit-23520121` → **Data Explorer** → `credit-assistant` → `feedback` → Items.

Ảnh minh chứng T39: web có nút Thích/Không thích đã bấm, kèm bản ghi tương ứng trong Data Explorer.

## Việc tiếp theo

- **T39:** chụp ảnh Data Explorer (bản ghi feedback) cho báo cáo.
