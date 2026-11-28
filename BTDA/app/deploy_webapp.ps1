# T40 – Deploy web demo lên Azure App Service (Linux, Python 3.12, gói F1 miễn phí).
# Chạy lại được nhiều lần: lần đầu tạo plan + web app, các lần sau chỉ cập nhật code/cấu hình.
#
#   cd BTDA\app
#   .\deploy_webapp.ps1                     # dùng tên mặc định
#   .\deploy_webapp.ps1 -Name <tên-khác>    # nếu tên mặc định đã có người dùng
#
# URL/key endpoint lấy từ file .env (không commit) và đưa vào App settings của web app
# (biến môi trường trên Azure) – không nằm trong code hay gói deploy.
param(
    [string]$Name = "app-credit-assistant-is402",
    [string]$ResourceGroup = "rg-credit-assistant",
    [string]$Location = "japaneast",
    [string]$Plan = "plan-credit-assistant",
    [string]$Sku = "F1"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Az {
    $out = & az @args --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "az $($args[0..2] -join ' ') lỗi (exit $LASTEXITCODE)" }
    return $out
}

# 1. App Service plan (Linux) + web app
# Kiểm tra tồn tại bằng "list" (lệnh "show" ghi lỗi ra stderr khi chưa có -> PowerShell 5.1 dừng script)
$planExists = Invoke-Az appservice plan list -g $ResourceGroup --query "[?name=='$Plan'].name" -o tsv
if (-not $planExists) {
    Write-Host "Tạo App Service plan $Plan ($Sku, Linux)..."
    Invoke-Az appservice plan create -n $Plan -g $ResourceGroup -l $Location --sku $Sku --is-linux -o none
}
$appExists = Invoke-Az webapp list -g $ResourceGroup --query "[?name=='$Name'].name" -o tsv
if (-not $appExists) {
    Write-Host "Tạo web app $Name..."
    Invoke-Az webapp create -n $Name -g $ResourceGroup -p $Plan --runtime "PYTHON:3.12" -o none
}

# 2. Cấu hình: lệnh khởi động, HTTPS, build khi deploy, URL/key từ .env
Invoke-Az webapp config set -n $Name -g $ResourceGroup --startup-file `
    "gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120 main:app" `
    --ftps-state Disabled --min-tls-version 1.2 -o none
Invoke-Az webapp update -n $Name -g $ResourceGroup --https-only true -o none

# Managed identity (T39): web app ghi Cosmos DB bằng Entra ID, không dùng key.
# Lệnh assign chạy lại nhiều lần vẫn giữ nguyên identity cũ. Owner (Bảo) cấp role
# "Cosmos DB Built-in Data Contributor" cho Object ID này trên database credit-assistant.
$principalId = Invoke-Az webapp identity assign -n $Name -g $ResourceGroup --query principalId -o tsv
Write-Host "Managed identity của web app – Object ID: $principalId"

$settings = @("SCM_DO_BUILD_DURING_DEPLOYMENT=true")
if (Test-Path .env) {
    foreach ($line in Get-Content .env) {
        if ($line -match '^\s*(CLASSIFIER_|CHAT_|COSMOS_|ENDPOINT_TIMEOUT|MOCK_MODE)[A-Z_]*=\S') { $settings += $line.Trim() }
    }
}
Invoke-Az webapp config appsettings set -n $Name -g $ResourceGroup --settings @settings -o none
Write-Host "Đã đặt $($settings.Count) app setting (giá trị không in ra)."

# 3. Đóng gói chỉ những file cần chạy (không có .env, .venv, outputs, test) và deploy
$pkg = Join-Path ([IO.Path]::GetTempPath()) "credit-assistant-web.zip"
if (Test-Path $pkg) { Remove-Item $pkg }
# Tự tạo zip với dấu "/" (Compress-Archive của PowerShell 5.1 ghi "\" -> Linux không nhận thư mục static/)
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::Open($pkg, "Create")
try {
    $files = @(Get-Item main.py, requirements.txt) + @(Get-ChildItem static -Recurse -File)
    foreach ($f in $files) {
        $entry = $f.FullName.Substring($PSScriptRoot.Length + 1).Replace("\", "/")
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $entry)
    }
} finally { $zip.Dispose() }
Write-Host "Deploy code (Azure cài thư viện từ requirements.txt, mất vài phút)..."
Invoke-Az webapp deploy -n $Name -g $ResourceGroup --src-path $pkg --type zip -o none
Remove-Item $pkg

$url = "https://" + (Invoke-Az webapp show -n $Name -g $ResourceGroup --query defaultHostName -o tsv)
Write-Host "Xong: $url"
try { Invoke-RestMethod "$url/api/health" -TimeoutSec 120 | ConvertTo-Json -Compress | Write-Host }
catch { Write-Host "Web đang khởi động, thử lại $url/api/health sau 1-2 phút." }
