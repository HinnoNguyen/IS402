// T16 – Hạ tầng dạng code (IaC) cho rg-credit-assistant.
// Mô tả các tài nguyên chính đúng với cấu hình đang chạy, để có thể dựng lại toàn bộ hệ thống ở RG khác.
//
// Kiểm tra không đổi gì trên Azure (chỉ so sánh):
//   az deployment group what-if -g rg-credit-assistant -f BTDA/infra/main.bicep
// Dựng mới ở RG khác (đổi suffix để tên toàn cục không trùng):
//   az group create -n rg-credit-assistant-2 -l japaneast
//   az deployment group create -g rg-credit-assistant-2 -f BTDA/infra/main.bicep -p suffix=<mới> deployModels=false
//
// Không có secret trong file: web app và Function App dùng managed identity; role được gán ở cuối file.
// Online endpoint và deployment (blue/green, Prompt Flow) tạo bằng YAML của Azure ML (BTDA/azureml/deployment).

@description('Region – Azure Policy của gói Students chỉ cho phép 5 region châu Á')
@allowed(['japaneast', 'koreacentral', 'eastasia', 'malaysiawest', 'indonesiacentral'])
param location string = 'japaneast'

@description('Hậu tố cho tên có tính toàn cục (storage, cosmos, web)')
param suffix string = '23520121'

@description('Tạo deployment model gpt-4.1-mini và text-embedding-3-small (cần quota)')
param deployModels bool = true

var tags = { project: 'IS402-BTDA', owner: 'nhom-2' }

// ---------------- Data Lake (ADLS Gen2) ----------------
resource lake 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stcredit${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true              // thư mục thật cho Medallion raw/processed
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}
resource lakeBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: lake
  name: 'default'
}
resource lakeContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in ['raw', 'processed', 'rag-docs']: {
  parent: lakeBlob
  name: c
  properties: { publicAccess: 'None' }
}]

// ---------------- Cosmos DB (feedback, Free Tier) ----------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-credit-${suffix}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: true
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [ { locationName: location, failoverPriority: 0, isZoneRedundant: false } ]
  }
}
resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'credit-assistant'
  properties: {
    resource: { id: 'credit-assistant' }
    options: { throughput: 400 }    // nằm trong mức miễn phí 1.000 RU/s
  }
}
resource feedback 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'feedback'
  properties: {
    resource: { id: 'feedback', partitionKey: { paths: ['/session_id'], kind: 'Hash' } }
  }
}

// ---------------- Azure AI Search (Free) + AI Services ----------------
resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: 'srch-credit-assistant'
  location: location
  tags: tags
  sku: { name: 'free' }
  properties: { replicaCount: 1, partitionCount: 1 }
}
resource ais 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'ais-credit-assistant'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: { customSubDomainName: 'ais-credit-assistant', publicNetworkAccess: 'Enabled' }
}
resource gpt 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployModels) {
  parent: ais
  name: 'gpt-4.1-mini'
  sku: { name: 'GlobalStandard', capacity: 100 }   // 100K token/phút; quota GPT-4o = 0 nên dùng gpt-4.1-mini
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
    raiPolicyName: 'credit-assistant-guardrail'      // guardrail tạo từ BTDA/genai/azure/rai_policy.json
  }
}
resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = if (deployModels) {
  parent: ais
  name: 'text-embedding-3-small'
  dependsOn: [ gpt ]                                 // tạo tuần tự, tránh xung đột khi cập nhật account
  sku: { name: 'GlobalStandard', capacity: 50 }
  properties: { model: { format: 'OpenAI', name: 'text-embedding-3-small', version: '1' } }
}

// ---------------- Azure Machine Learning ----------------
// Key Vault, Application Insights, Storage, Container Registry đi kèm workspace (đã tạo cùng workspace)
resource mlKv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: 'mlwcredikeyvaultbaf85a1d' }
resource mlAi 'Microsoft.Insights/components@2020-02-02' existing = { name: 'mlwcrediinsightsb2eed4f7' }
resource mlSt 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: 'mlwcredistorage0b3d66997' }
resource mlAcr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: 'caf9e3a7194e4e7993a4f855b82e8973' }

resource mlw 'Microsoft.MachineLearningServices/workspaces@2024-10-01' = {
  name: 'mlw-credit-assistant'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    friendlyName: 'Credit Assistant MLOps'
    keyVault: mlKv.id
    applicationInsights: mlAi.id
    storageAccount: mlSt.id
    containerRegistry: mlAcr.id
  }
}
resource cluster 'Microsoft.MachineLearningServices/workspaces/computes@2024-10-01' = {
  parent: mlw
  name: 'cpu-cluster'
  location: location
  identity: { type: 'SystemAssigned' }   // đọc ghi Data Lake qua datastore identity-based
  properties: {
    computeType: 'AmlCompute'
    properties: {
      vmSize: 'Standard_F4s_v2'
      scaleSettings: { minNodeCount: 0, maxNodeCount: 2, nodeIdleTimeBeforeScaleDown: 'PT10M' }   // về 0 node khi rảnh
    }
  }
}

// ---------------- Web demo (App Service F1) ----------------
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-credit-assistant'
  location: location
  tags: tags
  sku: { name: 'F1' }
  kind: 'linux'
  properties: { reserved: true }
}
resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: 'app-credit-assistant-is402'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120 main:app'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
  }
}

// ---------------- Azure Function xử lý phản hồi (Consumption) ----------------
resource funcSt 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stfunc${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: { minimumTlsVersion: 'TLS1_2', allowBlobPublicAccess: false }
}
resource funcPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${location == 'japaneast' ? 'JapanEast' : location}LinuxDynamicPlan'
  location: location
  sku: { name: 'Y1', tier: 'Dynamic' }
  kind: 'functionapp'
  properties: { reserved: true }
}
resource func 'Microsoft.Web/sites@2023-12-01' = {
  name: 'func-credit-feedback-is402'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: funcPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appSettings: [
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${funcSt.name};AccountKey=${funcSt.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
        { name: 'COSMOS_DATABASE', value: 'credit-assistant' }
        { name: 'COSMOS_CONTAINER', value: 'feedback' }
        { name: 'DATALAKE_ACCOUNT', value: lake.name }
        { name: 'DATALAKE_CONTAINER', value: 'processed' }
      ]
    }
  }
}

// ---------------- Phân quyền cho managed identity (quyền tối thiểu) ----------------
var blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource clusterLake 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: lake
  name: guid(lake.id, cluster.id, blobDataContributor)
  properties: { principalId: cluster.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributor) }
}
resource funcLake 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: lake
  name: guid(lake.id, func.id, blobDataContributor)
  properties: { principalId: func.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributor) }
}
// Cosmos DB data plane: web ghi (Built-in Data Contributor …0002), Function đọc (Built-in Data Reader …0001)
resource webCosmos 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, web.id, 'contributor')
  properties: { principalId: web.identity.principalId, roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002', scope: cosmosDb.id }
}
resource funcCosmos 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, func.id, 'reader')
  properties: { principalId: func.identity.principalId, roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000001', scope: cosmosDb.id }
}

output dataLake string = lake.name
output webUrl string = 'https://${web.properties.defaultHostName}'
output functionUrl string = 'https://${func.properties.defaultHostName}/api/feedback-report'
