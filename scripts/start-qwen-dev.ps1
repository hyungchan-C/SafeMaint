[CmdletBinding()]
param(
    [int]$ReadyTimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"
# Docker Compose Bake can generate an invalid session header when the Windows
# workspace path contains non-ASCII characters. The classic Compose builder is
# slower but reliable for this local development script.
$env:COMPOSE_BAKE = "false"
$Root = Split-Path -Parent $PSScriptRoot
$ComposeArgs = @(
    "-f", (Join-Path $Root "docker-compose.yml"),
    "-f", (Join-Path $Root "docker-compose.dev.yml"),
    "-f", (Join-Path $Root "docker-compose.qwen.yml")
)
$GeneralModel = if ($env:QWEN_GENERAL_MODEL) {
    $env:QWEN_GENERAL_MODEL
} else {
    "qwen3.5:4b"
}
$ModelRoot = Join-Path $Root "safemaint_api_data\safemaint_qwen35_9b_finetuning_result\01_finetuned_model"
$RequiredArtifacts = @(
    (Join-Path $ModelRoot "model_manifest.json"),
    (Join-Path $ModelRoot "load_and_predict.py"),
    (Join-Path $ModelRoot "best_adapter\adapter_config.json"),
    (Join-Path $ModelRoot "best_adapter\adapter_model.safetensors"),
    (Join-Path $ModelRoot "tokenizer\tokenizer.json")
)

function Show-FailureLogs {
    Write-Host "`n[Diagnostic logs]" -ForegroundColor Yellow
    & docker compose @ComposeArgs logs --tail 200 qwen-classifier backend
}

try {
    Set-Location $Root
    Write-Host "[1/7] Checking Ollama and Docker"
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    $ollamaExe = if ($ollamaCommand) {
        $ollamaCommand.Source
    } else {
        Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    }
    if (-not (Test-Path -LiteralPath $ollamaExe -PathType Leaf)) {
        throw "Ollama is not installed or could not be found. Open a new PowerShell after installing it."
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "The docker command could not be found."
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Engine is not running."
    }

    Write-Host "[2/7] Checking team LoRA artifacts"
    foreach ($artifact in $RequiredArtifacts) {
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw "A required team model artifact is missing: $artifact"
        }
    }

    Write-Host "[3/7] Preparing the Ollama answer model: $GeneralModel"
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3 | Out-Null
    } catch {
        Write-Host "      Starting the Ollama server."
        Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden
        $ollamaDeadline = (Get-Date).AddSeconds(30)
        do {
            Start-Sleep -Seconds 1
            try {
                Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 3 | Out-Null
                $ollamaReady = $true
            } catch {
                $ollamaReady = $false
            }
        } while (-not $ollamaReady -and (Get-Date) -lt $ollamaDeadline)
        if (-not $ollamaReady) {
            throw "The Ollama server could not be started."
        }
    }
    & $ollamaExe pull $GeneralModel
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to pull the Ollama model: $GeneralModel"
    }

    Write-Host "[4/7] Starting the team Qwen classifier and SafeMaint"
    $gitConfigNames = @("GIT_CONFIG_COUNT")
    $gitConfigCount = 0
    if ([int]::TryParse($env:GIT_CONFIG_COUNT, [ref]$gitConfigCount)) {
        for ($index = 0; $index -lt $gitConfigCount; $index++) {
            $gitConfigNames += "GIT_CONFIG_KEY_$index", "GIT_CONFIG_VALUE_$index"
        }
    }
    $gitConfigBackup = @{}
    $buildxGitInfoBackup = [Environment]::GetEnvironmentVariable("BUILDX_GIT_INFO", "Process")
    $buildxGitDirtyBackup = [Environment]::GetEnvironmentVariable("BUILDX_GIT_CHECK_DIRTY", "Process")
    foreach ($name in $gitConfigNames) {
        $gitConfigBackup[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    $env:BUILDX_GIT_INFO = "false"
    $env:BUILDX_GIT_CHECK_DIRTY = "false"
    try {
        $composeExitCode = 0
        foreach ($service in @("backend", "rag", "frontend", "qwen-classifier")) {
            Write-Host "      Building $service"
            & docker compose @ComposeArgs build $service
            if ($LASTEXITCODE -ne 0) {
                $composeExitCode = $LASTEXITCODE
                break
            }
        }
        if ($composeExitCode -eq 0) {
            & docker compose @ComposeArgs up -d --no-build
            $composeExitCode = $LASTEXITCODE
        }
    } finally {
        foreach ($name in $gitConfigNames) {
            [Environment]::SetEnvironmentVariable($name, $gitConfigBackup[$name], "Process")
        }
        [Environment]::SetEnvironmentVariable("BUILDX_GIT_INFO", $buildxGitInfoBackup, "Process")
        [Environment]::SetEnvironmentVariable("BUILDX_GIT_CHECK_DIRTY", $buildxGitDirtyBackup, "Process")
    }
    if ($composeExitCode -ne 0) {
        throw "Docker Compose failed to start."
    }

    Write-Host "[5/7] Waiting for the team Qwen3.5-9B + LoRA classifier"
    Write-Host "      The first run may take a long time while the base model is downloaded from Hugging Face."
    $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
    $lastStatus = ""
    while ((Get-Date) -lt $deadline) {
        $containerId = (& docker compose @ComposeArgs ps -q qwen-classifier).Trim()
        if ($containerId) {
            $status = (& docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $containerId).Trim()
            if ($status -ne $lastStatus) {
                Write-Host "      qwen-classifier: $status"
                $lastStatus = $status
            }
            if ($status -eq "healthy") {
                break
            }
            if ($status -eq "exited" -or $status -eq "dead") {
                throw "The qwen-classifier container stopped unexpectedly."
            }
        }
        Start-Sleep -Seconds 10
    }
    if ($lastStatus -ne "healthy") {
        throw "The team Qwen classifier did not become ready before the timeout."
    }

    Write-Host "[6/7] Sending a real classification request to the team LoRA"
    $probeCode = 'import json,urllib.request; data=json.dumps({"title":"\ucee8\ubca0\uc774\uc5b4 \ubca0\uc5b4\ub9c1 \uad50\uccb4","text":"\uac00\ub3d9 \uc911\uc778 \ub864\ub7ec\uc5d0 \uc190\uc774 \ub9d0\ub824 \ub4e4\uc5b4\uac08 \uc704\ud5d8\uc774 \uc788\ub2e4."}).encode(); req=urllib.request.Request("http://localhost:8030/v1/classify",data=data,headers={"Content-Type":"application/json"}); print(urllib.request.urlopen(req,timeout=300).read().decode())'
    $probeEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($probeCode))
    $probeCommand = "import base64;exec(base64.b64decode('$probeEncoded'))"
    & docker compose @ComposeArgs exec -T qwen-classifier python -c $probeCommand
    if ($LASTEXITCODE -ne 0) {
        throw "The real classification request to the team LoRA failed."
    }

    Write-Host "[7/7] Checking service status"
    & docker compose @ComposeArgs ps
    Write-Host "`nSafeMaint team Qwen environment is ready: http://localhost:3000" -ForegroundColor Green
} catch {
    Write-Host "`nFailed to start the SafeMaint team Qwen environment: $($_.Exception.Message)" -ForegroundColor Red
    try { Show-FailureLogs } catch { }
    exit 1
}
