[CmdletBinding()]
param(
    [switch]$RunExternalLlm,
    [switch]$KeepServices
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFiles = @(
    "-f", (Join-Path $repoRoot "docker-compose.yml"),
    "-f", (Join-Path $repoRoot "docker-compose.e2e.yml")
)
$savedLocation = Get-Location
$savedEnvironment = @{}
$composeEnvironmentReady = $false
$environmentNames = @(
    "COMPOSE_PROJECT_NAME", "POSTGRES_DB", "POSTGRES_USER",
    "POSTGRES_PASSWORD", "DATABASE_URL", "POSTGRES_VOLUME_NAME",
    "DOCUMENT_VOLUME_NAME", "PACKAGE_VOLUME_NAME", "BACKEND_PORT",
    "RUN_RAG_E2E", "RUN_EXTERNAL_LLM_E2E", "ALLOW_TEST_DB_MUTATION",
    "E2E_USER_PASSWORD", "E2E_RUN_ID", "ALLOW_EXTERNAL_LLM",
    "LLM_BASE_URL", "LLM_ANALYZER_MODEL", "LLM_ANSWER_MODEL"
)
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose @composeFiles @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose E2E command failed with exit code $LASTEXITCODE."
    }
}

try {
    Set-Location $repoRoot
    docker version --format "{{.Server.Version}}" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Engine is not available. Start Docker Desktop and retry."
    }

    $runId = [guid]::NewGuid().ToString("N").Substring(0, 12)
    $env:COMPOSE_PROJECT_NAME = "safemaint-pdf-rag-e2e"
    $env:POSTGRES_DB = "safemaint_e2e_test"
    $env:POSTGRES_USER = "safemaint"
    $env:POSTGRES_PASSWORD = "E2EDb$([guid]::NewGuid().ToString('N'))"
    $env:DATABASE_URL = "postgresql+psycopg://safemaint:$($env:POSTGRES_PASSWORD)@db:5432/safemaint_e2e_test"
    # Every run receives new test-only storage so a preserved PostgreSQL volume
    # can never retain credentials from an earlier run. The model cache remains
    # shared because it contains no document or database data.
    $env:POSTGRES_VOLUME_NAME = "safemaint_e2e_postgres_$runId"
    $env:DOCUMENT_VOLUME_NAME = "safemaint_e2e_documents_$runId"
    $env:PACKAGE_VOLUME_NAME = "safemaint_e2e_packages_$runId"
    $env:BACKEND_PORT = "18000"
    $env:RUN_RAG_E2E = "1"
    $env:ALLOW_TEST_DB_MUTATION = "1"
    $env:E2E_USER_PASSWORD = "E2E-$([guid]::NewGuid().ToString('N'))-Aa1!"
    $env:E2E_RUN_ID = $runId
    $env:RUN_EXTERNAL_LLM_E2E = "0"
    $env:ALLOW_EXTERNAL_LLM = "false"
    $composeEnvironmentReady = $true

    if ($RunExternalLlm) {
        if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
            Write-Warning "OPENAI_API_KEY is unavailable; external LLM E2E will be skipped."
        }
        else {
            $env:RUN_EXTERNAL_LLM_E2E = "1"
            $env:ALLOW_EXTERNAL_LLM = "true"
            $env:LLM_BASE_URL = ""
            $env:LLM_ANALYZER_MODEL = "gpt-4o-mini"
            $env:LLM_ANSWER_MODEL = "gpt-4o-mini"
        }
    }

    Write-Host "[1/5] Building current backend, RAG, worker, and E2E images"
    # backend/migrate/seed and rag/worker share build definitions. Building the
    # shared images concurrently can make Docker Desktop reuse a broken BuildKit
    # session, so build each unique image once in a deterministic order.
    Invoke-Compose -Arguments @("--profile", "e2e", "build", "backend")
    Invoke-Compose -Arguments @("--profile", "e2e", "build", "rag")
    Invoke-Compose -Arguments @("--profile", "e2e", "build", "e2e")

    Write-Host "[2/5] Starting isolated DB, migrations, seed, backend, RAG, and worker"
    Invoke-Compose -Arguments @("--profile", "e2e", "up", "-d", "db", "migrate", "seed", "backend", "rag", "worker")

    Write-Host "[3/5] Checking isolated service status"
    Invoke-Compose -Arguments @("--profile", "e2e", "ps", "-a")

    Write-Host "[4/5] Running fake-PDF upload, worker, BGE-M3, RAG, and optional LLM E2E"
    Invoke-Compose -Arguments @("--profile", "e2e", "run", "--rm", "e2e")

    Write-Host "[5/5] PDF RAG E2E completed successfully"
}
catch {
    Write-Host "SafeMaint PDF RAG E2E failed: $($_.Exception.Message)" -ForegroundColor Red
    try {
        if ($composeEnvironmentReady) {
            & docker compose @composeFiles --profile e2e logs --tail 200 db migrate seed worker backend rag
        }
    }
    catch {
        Write-Warning "Unable to collect E2E service logs."
    }
    throw
}
finally {
    if ($composeEnvironmentReady -and -not $KeepServices) {
        try {
            & docker compose @composeFiles --profile e2e down --remove-orphans | Out-Null
        }
        catch {
            Write-Warning "Unable to stop E2E services automatically."
        }
    }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    Set-Location $savedLocation
}
