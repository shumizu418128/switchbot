$ErrorActionPreference = "Stop"

$stackName = if ($env:STACK_NAME) { $env:STACK_NAME } else { "switchbot-co2-check" }
$templateFile = if ($env:TEMPLATE_FILE) { $env:TEMPLATE_FILE } else { "template.yaml" }
$s3Prefix = if ($env:S3_PREFIX) { $env:S3_PREFIX } else { "switchbot-co2-check" }
$awsRegion = "ap-northeast-1"
$envFilePath = ".env"

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Error "aws CLI not found. Please install it."
}

if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
    Write-Error "AWS SAM CLI not found. Please install it."
}

if (-not (Test-Path -LiteralPath $templateFile -PathType Leaf)) {
    Write-Error "$templateFile not found."
}

if (-not (Test-Path -LiteralPath $envFilePath -PathType Leaf)) {
    Write-Error ".env not found."
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

$requiredEnvVars = @("TOKEN", "CLIENT_SECRET", "SLACK_WEBHOOK_URL", "SLACK_SIGNING_SECRET", "HOME_WIFI_SSID")
$missingVars = @()
foreach ($requiredKey in $requiredEnvVars) {
    if ([string]::IsNullOrWhiteSpace((Get-Item -Path ("Env:{0}" -f $requiredKey) -ErrorAction SilentlyContinue).Value)) {
        $missingVars += $requiredKey
    }
}

if ($missingVars.Count -gt 0) {
    Write-Error ".env is missing required keys: $($missingVars -join ', ')"
}

$awsProfile = $env:AWS_PROFILE

if (-not $env:AWS_PROFILE) {
    Write-Error "AWS_PROFILE is not set. Please set it first."
}

Write-Host ""
Write-Host "Checking AWS authentication..."

& aws sts get-caller-identity --profile $awsProfile *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged into AWS; running 'aws sso login' (profile: $awsProfile)..."
    & aws sso login --profile $awsProfile
}

& aws sts get-caller-identity --profile $awsProfile *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to verify AWS authentication. Please check profile=$awsProfile."
}

Write-Host ""
Write-Host "building..."
& sam build --template-file $templateFile

Write-Host ""
Write-Host "Checking notification state parameter..."

$switchBotApiBaseUrl = if ([string]::IsNullOrWhiteSpace($env:SWITCHBOT_API_BASE_URL)) { "https://api.switch-bot.com" } else { $env:SWITCHBOT_API_BASE_URL }
$co2AlertStateParamName = "/$stackName/CO2_ALERT_STATE"
$co2AlertStateInitialValue = '{"alert_active":false,"last_alert_type":null,"updated_at":null,"humidity_alert_active":false}'
$co2ParamCheckOutput = (& aws ssm get-parameter `
    --name $co2AlertStateParamName `
    --region $awsRegion `
    --profile $awsProfile 2>&1)

if ($LASTEXITCODE -ne 0) {
    if (($co2ParamCheckOutput -join "`n") -match "ParameterNotFound") {
        Write-Host "Creating notification state parameter: $co2AlertStateParamName"
        & aws ssm put-parameter `
            --name $co2AlertStateParamName `
            --value $co2AlertStateInitialValue `
            --type "SecureString" `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to initialize notification state parameter: $co2AlertStateParamName"
        }
    } else {
        Write-Host ($co2ParamCheckOutput -join "`n")
        Write-Error "Failed to check notification state parameter: $co2AlertStateParamName"
    }
}

Write-Host ""
Write-Host "Checking humidity history parameter..."

$humidityHistoryParamName = "/$stackName/HUMIDITY_HISTORY"
$humidityHistoryInitialValue = '[]'
$humidityHistoryParamCheckOutput = (& aws ssm get-parameter `
    --name $humidityHistoryParamName `
    --region $awsRegion `
    --profile $awsProfile 2>&1)

if ($LASTEXITCODE -ne 0) {
    if (($humidityHistoryParamCheckOutput -join "`n") -match "ParameterNotFound") {
        Write-Host "Creating humidity history parameter: $humidityHistoryParamName"
        & aws ssm put-parameter `
            --name $humidityHistoryParamName `
            --value $humidityHistoryInitialValue `
            --type "String" `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to initialize humidity history parameter: $humidityHistoryParamName"
        }
    } else {
        Write-Host ($humidityHistoryParamCheckOutput -join "`n")
        Write-Error "Failed to check humidity history parameter: $humidityHistoryParamName"
    }
}

Write-Host ""
Write-Host "Checking at-home state parameter..."

$wifiSsidParamName = "/$stackName/WIFI_SSID"
$wifiSsidInitialValue = '{"at_home":false,"updated_at":null}'
$wifiParamCheckOutput = (& aws ssm get-parameter `
    --name $wifiSsidParamName `
    --region $awsRegion `
    --profile $awsProfile 2>&1)

if ($LASTEXITCODE -ne 0) {
    if (($wifiParamCheckOutput -join "`n") -match "ParameterNotFound") {
        Write-Host "Creating at-home state parameter: $wifiSsidParamName"
        & aws ssm put-parameter `
            --name $wifiSsidParamName `
            --value $wifiSsidInitialValue `
            --type "SecureString" `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to initialize at-home state parameter: $wifiSsidParamName"
        }
    } else {
        Write-Host ($wifiParamCheckOutput -join "`n")
        Write-Error "Failed to check at-home state parameter: $wifiSsidParamName"
    }
}

Write-Host ""
Write-Host "Checking lock notification state parameter..."

$lockAlertStateParamName = "/$stackName/LOCK_ALERT_STATE"
$lockAlertStateInitialValue = '{"alert_active":false,"abnormal_since":null,"updated_at":null}'
$lockParamCheckOutput = (& aws ssm get-parameter `
    --name $lockAlertStateParamName `
    --region $awsRegion `
    --profile $awsProfile 2>&1)

if ($LASTEXITCODE -ne 0) {
    if (($lockParamCheckOutput -join "`n") -match "ParameterNotFound") {
        Write-Host "Creating lock notification state parameter: $lockAlertStateParamName"
        & aws ssm put-parameter `
            --name $lockAlertStateParamName `
            --value $lockAlertStateInitialValue `
            --type "SecureString" `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to initialize lock notification state parameter: $lockAlertStateParamName"
        }
    } else {
        Write-Host ($lockParamCheckOutput -join "`n")
        Write-Error "Failed to check lock notification state parameter: $lockAlertStateParamName"
    }
}

Write-Host ""
Write-Host "deploying..."

$parameterOverrides = @(
    "Token=$($env:TOKEN)",
    "ClientSecret=$($env:CLIENT_SECRET)",
    "SlackWebhookUrl=$($env:SLACK_WEBHOOK_URL)",
    "SlackSigningSecret=$($env:SLACK_SIGNING_SECRET)",
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
    Write-Error "Deployment failed."
}

Write-Host ""
Write-Host "Retrieving API Gateway information..."

$stackOutputsJson = & aws cloudformation describe-stacks `
    --stack-name $stackName `
    --region $awsRegion `
    --profile $awsProfile `
    --query "Stacks[0].Outputs" `
    --output json

if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($stackOutputsJson)) {
    Write-Error "Failed to retrieve stack outputs."
}

$stackOutputs = $stackOutputsJson | ConvertFrom-Json
$apiKeyId = ($stackOutputs | Where-Object { $_.OutputKey -eq "SwitchBotApiKeyId" }).OutputValue
$wifiEndpoint = ($stackOutputs | Where-Object { $_.OutputKey -eq "SwitchBotWifiEndpoint" }).OutputValue
$slackInteractionsEndpoint = ($stackOutputs | Where-Object { $_.OutputKey -eq "SwitchBotSlackInteractionsEndpoint" }).OutputValue

if ([string]::IsNullOrWhiteSpace($apiKeyId)) {
    Write-Error "Output for SwitchBotApiKeyId not found."
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
    Write-Error "Failed to retrieve API key value."
}

Write-Host ""
Write-Host "API Gateway settings:"
Write-Host "  WiFi endpoint: $wifiEndpoint"
Write-Host "  Slack Interactivity (Request URL): $slackInteractionsEndpoint"
Write-Host "  API key (x-api-key): $apiKeyValueOutput"
Write-Host ""
Write-Host "Example call:"
Write-Host ('  curl -X POST "' + $wifiEndpoint + '" -H "x-api-key: ' + $apiKeyValueOutput + '" -H "Content-Type: application/json" -d "{\"event\":\"wifi_connected\",\"ssid\":\"YOUR_SSID\",\"timestamp\":\"2026-05-20T12:00:00+09:00\"}"')

Write-Host ""
Write-Host ("Deployment completed: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
