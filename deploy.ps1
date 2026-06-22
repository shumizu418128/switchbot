$ErrorActionPreference = "Stop"

$stackName = if ($env:STACK_NAME) { $env:STACK_NAME } else { "switchbot-co2-check" }
$templateFile = if ($env:TEMPLATE_FILE) { $env:TEMPLATE_FILE } else { "template.yaml" }
$s3Prefix = if ($env:S3_PREFIX) { $env:S3_PREFIX } else { "switchbot-co2-check" }
$awsRegion = "ap-northeast-1"
$envFilePath = ".env"

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Error "aws CLI が見つかりません。インストールしてください。"
}

if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
    Write-Error "AWS SAM CLI が見つかりません。インストールしてください。"
}

if (-not (Test-Path -LiteralPath $templateFile -PathType Leaf)) {
    Write-Error "$templateFile が見つかりません。"
}

if (-not (Test-Path -LiteralPath $envFilePath -PathType Leaf)) {
    Write-Error ".env が見つかりません。"
}

# .env の KEY=VALUE を環境変数として現在セッションに読み込む
Get-Content -LiteralPath $envFilePath | ForEach-Object {
    $line = $_.Trim()
    if ([string]::IsNullOrWhiteSpace($line)) { return }
    if ($line.StartsWith("#")) { return }

    $pair = $line -split "=", 2
    if ($pair.Count -ne 2) { return }

    $key = $pair[0].Trim()
    $value = $pair[1].Trim()

    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    Set-Item -Path ("Env:{0}" -f $key) -Value $value
}

$requiredEnvVars = @("TOKEN", "CLIENT_SECRET", "SLACK_WEBHOOK_URL", "HOME_WIFI_SSID")
$missingVars = @()
foreach ($requiredKey in $requiredEnvVars) {
    if ([string]::IsNullOrWhiteSpace((Get-Item -Path ("Env:{0}" -f $requiredKey) -ErrorAction SilentlyContinue).Value)) {
        $missingVars += $requiredKey
    }
}

if ($missingVars.Count -gt 0) {
    Write-Error ".env の必須キーが不足しています: $($missingVars -join ', ')"
}

$awsProfile = $env:AWS_PROFILE

if (-not $env:AWS_PROFILE) {
    Write-Error "AWS_PROFILE が未設定です。先に設定してください。"
}

Write-Host ""
Write-Host "AWS 認証状態を確認しています..."

& aws sts get-caller-identity --profile $awsProfile *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "AWS にログインしていないため、aws sso login を実行します（profile: $awsProfile）..."
    & aws sso login --profile $awsProfile
}

& aws sts get-caller-identity --profile $awsProfile *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "AWS 認証の確認に失敗しました。profile=$awsProfile を確認してください。"
}

Write-Host ""
Write-Host "building..."
& sam build --template-file $templateFile

Write-Host ""
Write-Host "通知状態パラメータを確認しています..."

$switchBotApiBaseUrl = if ([string]::IsNullOrWhiteSpace($env:SWITCHBOT_API_BASE_URL)) { "https://api.switch-bot.com" } else { $env:SWITCHBOT_API_BASE_URL }
$co2AlertStateParamName = "/$stackName/CO2_ALERT_STATE"
$co2AlertStateInitialValue = '{"alert_active":false,"last_alert_type":null,"updated_at":null,"last_humidity_alert_at":null}'
$co2ParamCheckOutput = (& aws ssm get-parameter `
    --name $co2AlertStateParamName `
    --region $awsRegion `
    --profile $awsProfile 2>&1)

if ($LASTEXITCODE -ne 0) {
    if (($co2ParamCheckOutput -join "`n") -match "ParameterNotFound") {
        Write-Host "通知状態パラメータを初期作成します: $co2AlertStateParamName"
        & aws ssm put-parameter `
            --name $co2AlertStateParamName `
            --value $co2AlertStateInitialValue `
            --type "SecureString" `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "通知状態パラメータの初期作成に失敗しました: $co2AlertStateParamName"
        }
    } else {
        Write-Host ($co2ParamCheckOutput -join "`n")
        Write-Error "通知状態パラメータの確認に失敗しました: $co2AlertStateParamName"
    }
}

Write-Host ""
Write-Host "在宅状態パラメータを確認しています..."

$wifiSsidParamName = "/$stackName/WIFI_SSID"
$wifiSsidInitialValue = '{"at_home":false,"updated_at":null}'
$wifiParamCheckOutput = (& aws ssm get-parameter `
    --name $wifiSsidParamName `
    --region $awsRegion `
    --profile $awsProfile 2>&1)

if ($LASTEXITCODE -ne 0) {
    if (($wifiParamCheckOutput -join "`n") -match "ParameterNotFound") {
        Write-Host "在宅状態パラメータを初期作成します: $wifiSsidParamName"
        & aws ssm put-parameter `
            --name $wifiSsidParamName `
            --value $wifiSsidInitialValue `
            --type "SecureString" `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "在宅状態パラメータの初期作成に失敗しました: $wifiSsidParamName"
        }
    } else {
        Write-Host ($wifiParamCheckOutput -join "`n")
        Write-Error "在宅状態パラメータの確認に失敗しました: $wifiSsidParamName"
    }
}

Write-Host ""
Write-Host "deploying..."

$parameterOverrides = @(
    "Token=$($env:TOKEN)",
    "ClientSecret=$($env:CLIENT_SECRET)",
    "SlackWebhookUrl=$($env:SLACK_WEBHOOK_URL)",
    "SwitchBotApiBaseUrl=$switchBotApiBaseUrl",
    "HomeWifiSsid=$($env:HOME_WIFI_SSID)"
)

if (-not [string]::IsNullOrWhiteSpace($env:API_KEY)) {
    $parameterOverrides += "ApiKeyValue=$($env:API_KEY)"
}

& sam deploy `
    --stack-name $stackName `
    --region $awsRegion `
    --profile $awsProfile `
    --capabilities CAPABILITY_IAM `
    --parameter-overrides $parameterOverrides `
    --resolve-s3 `
    --s3-prefix $s3Prefix

if ($LASTEXITCODE -ne 0) {
    Write-Error "デプロイに失敗しました。"
}

Write-Host ""
Write-Host "API Gateway 情報を取得しています..."

$stackOutputsJson = (& aws cloudformation describe-stacks `
    --stack-name $stackName `
    --region $awsRegion `
    --profile $awsProfile `
    --query "Stacks[0].Outputs" `
    --output json 2>&1)

if ($LASTEXITCODE -ne 0) {
    Write-Host ($stackOutputsJson -join "`n")
    Write-Error "スタック出力の取得に失敗しました。"
}

$stackOutputs = $stackOutputsJson | ConvertFrom-Json
$apiKeyId = ($stackOutputs | Where-Object { $_.OutputKey -eq "SwitchBotApiKeyId" }).OutputValue
$wifiEndpoint = ($stackOutputs | Where-Object { $_.OutputKey -eq "SwitchBotWifiEndpoint" }).OutputValue

if ([string]::IsNullOrWhiteSpace($apiKeyId)) {
    Write-Error "SwitchBotApiKeyId の出力が見つかりません。"
}

$apiKeyValueOutput = (& aws apigateway get-api-key `
    --api-key $apiKeyId `
    --include-value `
    --region $awsRegion `
    --profile $awsProfile `
    --query "value" `
    --output text 2>&1)

if ($LASTEXITCODE -ne 0) {
    Write-Host ($apiKeyValueOutput -join "`n")
    Write-Error "API キー値の取得に失敗しました。"
}

Write-Host ""
Write-Host "API Gateway 設定:"
Write-Host "  WiFi endpoint: $wifiEndpoint"
Write-Host "  API key (x-api-key): $apiKeyValueOutput"
Write-Host ""
Write-Host "呼び出し例:"
Write-Host ('  curl -X POST "' + $wifiEndpoint + '" -H "x-api-key: ' + $apiKeyValueOutput + '" -H "Content-Type: application/json" -d "{\"event\":\"wifi_connected\",\"ssid\":\"YOUR_SSID\",\"timestamp\":\"2026-05-20T12:00:00+09:00\"}"')

Write-Host ""
Write-Host ("デプロイ完了: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
