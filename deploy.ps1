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

$requiredEnvVars = @("TOKEN", "CLIENT_SECRET", "SLACK_WEBHOOK_URL")
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
Write-Host "SSM パラメータを更新しています..."

$switchBotApiBaseUrl = if ([string]::IsNullOrWhiteSpace($env:SWITCHBOT_API_BASE_URL)) { "https://api.switch-bot.com" } else { $env:SWITCHBOT_API_BASE_URL }
$co2AlertStateParamName = "/$stackName/CO2_ALERT_STATE"
$co2AlertStateInitialValue = '{"alert_active":false,"last_alert_type":null,"updated_at":null}'
$ssmParams = @(
    @{ Name = "/$stackName/TOKEN"; Value = $env:TOKEN; Type = "SecureString" },
    @{ Name = "/$stackName/CLIENT_SECRET"; Value = $env:CLIENT_SECRET; Type = "SecureString" },
    @{ Name = "/$stackName/SLACK_WEBHOOK_URL"; Value = $env:SLACK_WEBHOOK_URL; Type = "SecureString" },
    @{ Name = "/$stackName/SWITCHBOT_API_BASE_URL"; Value = $switchBotApiBaseUrl; Type = "SecureString" }
)

foreach ($param in $ssmParams) {
    $putOutput = (& aws ssm put-parameter `
        --name $param.Name `
        --value $param.Value `
        --type $param.Type `
        --overwrite `
        --region $awsRegion `
        --profile $awsProfile 2>&1)
    $putExitCode = $LASTEXITCODE

    if ($putExitCode -eq 0) {
        continue
    }

    if (($putOutput -join "`n") -match "different type") {
        Write-Host "既存パラメータの型が異なるため再作成します: $($param.Name)"
        & aws ssm delete-parameter `
            --name $param.Name `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "SSM パラメータ削除に失敗しました: $($param.Name)"
        }

        & aws ssm put-parameter `
            --name $param.Name `
            --value $param.Value `
            --type $param.Type `
            --overwrite `
            --region $awsRegion `
            --profile $awsProfile 2>&1 | Out-Host

        if ($LASTEXITCODE -ne 0) {
            Write-Error "SSM パラメータ更新に失敗しました: $($param.Name)"
        }
    } else {
        Write-Host ($putOutput -join "`n")
        Write-Error "SSM パラメータ更新に失敗しました: $($param.Name)"
    }
}

Write-Host "通知状態パラメータを確認しています..."
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
Write-Host "deploying..."

& sam deploy `
    --stack-name $stackName `
    --region $awsRegion `
    --profile $awsProfile `
    --capabilities CAPABILITY_IAM `
    --resolve-s3 `
    --s3-prefix $s3Prefix

if ($LASTEXITCODE -ne 0) {
    Write-Error "デプロイに失敗しました。"
}

Write-Host ""
Write-Host ("デプロイ完了: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
