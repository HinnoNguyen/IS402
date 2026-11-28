# T43 – Deploy Azure Function xử lý phản hồi "Không thích" (Consumption plan, Linux, Python 3.11).
# Chạy lại được nhiều lần. Cần quyền Owner (Bảo) để gán role cho managed identity.
#
#   cd BTDA\functions\feedback_report
#   .\deploy_function.ps1
param(
    [string]$Name = "func-credit-feedback-is402",
    [string]$ResourceGroup = "rg-credit-assistant",
    [string]$Location = "japaneast",
    [string]$FuncStorage = "stfunc23520121",          # storage riêng cho runtime của Function (không dùng Data Lake)
    [string]$DataLake = "stcredit23520121",
    [string]$Cosmos = "cosmos-credit-23520121",
    [string]$CosmosDb = "credit-assistant"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Az {
    $out = & az @args --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "az $($args[0..2] -join ' ') lỗi (exit $LASTEXITCODE)" }
    return $out
}

# 1. Storage cho runtime + Function App (Consumption)
if (-not (Invoke-Az storage account list -g $ResourceGroup --query "[?name=='$FuncStorage'].name" -o tsv)) {
    Write-Host "Tạo storage $FuncStorage..."
    Invoke-Az storage account create -n $FuncStorage -g $ResourceGroup -l $Location --sku Standard_LRS --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false -o none
}
if (-not (Invoke-Az functionapp list -g $ResourceGroup --query "[?name=='$Name'].name" -o tsv)) {
    Write-Host "Tạo Function App $Name..."
    Invoke-Az functionapp create -n $Name -g $ResourceGroup --storage-account $FuncStorage --consumption-plan-location $Location `
        --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux -o none
}
Invoke-Az functionapp update -n $Name -g $ResourceGroup --set httpsOnly=true -o none

# 2. Managed identity + quyền tối thiểu: đọc Cosmos DB (data plane), ghi Data Lake
$principalId = Invoke-Az functionapp identity assign -n $Name -g $ResourceGroup --query principalId -o tsv
Write-Host "Managed identity – Object ID: $principalId"
$cosmosRoles = Invoke-Az cosmosdb sql role assignment list -a $Cosmos -g $ResourceGroup --query "[?principalId=='$principalId'].id" -o tsv
if (-not $cosmosRoles) {
    Invoke-Az cosmosdb sql role assignment create -a $Cosmos -g $ResourceGroup -s "/dbs/$CosmosDb" -p $principalId `
        -d 00000000-0000-0000-0000-000000000001 -o none    # Cosmos DB Built-in Data Reader
}
$lakeId = Invoke-Az storage account show -n $DataLake -g $ResourceGroup --query id -o tsv
$hasBlob = Invoke-Az role assignment list --assignee $principalId --scope $lakeId --query "[?roleDefinitionName=='Storage Blob Data Contributor'].id" -o tsv
if (-not $hasBlob) {
    Invoke-Az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
        --role "Storage Blob Data Contributor" --scope $lakeId -o none
}

# 3. Cấu hình (không có secret: Cosmos và Data Lake dùng managed identity)
$cosmosEndpoint = Invoke-Az cosmosdb show -n $Cosmos -g $ResourceGroup --query documentEndpoint -o tsv
Invoke-Az functionapp config appsettings set -n $Name -g $ResourceGroup --settings `
    "COSMOS_ENDPOINT=$cosmosEndpoint" "COSMOS_DATABASE=$CosmosDb" "COSMOS_CONTAINER=feedback" `
    "DATALAKE_ACCOUNT=$DataLake" "DATALAKE_CONTAINER=processed" -o none

# 4. Đóng gói và deploy (Azure cài thư viện từ requirements.txt)
$pkg = Join-Path ([IO.Path]::GetTempPath()) "feedback-function.zip"
if (Test-Path $pkg) { Remove-Item $pkg }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::Open($pkg, "Create")
try {
    foreach ($f in Get-Item function_app.py, host.json, requirements.txt) {
        [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $f.FullName, $f.Name)
    }
} finally { $zip.Dispose() }
Write-Host "Deploy code (remote build, mất vài phút)..."
Invoke-Az functionapp deployment source config-zip -n $Name -g $ResourceGroup --src $pkg --build-remote true -o none
Remove-Item $pkg
Write-Host "Xong. Gọi thử: GET https://$Name.azurewebsites.net/api/feedback-report?code=<function key>"
Write-Host "Lấy key: az functionapp function keys list -n $Name -g $ResourceGroup --function-name feedback_report_http"
