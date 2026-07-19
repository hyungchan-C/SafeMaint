[CmdletBinding()]
param(
    [string]$EnvFile = ".env",
    [string]$ProjectName = "",
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dev-common.ps1")

$composeArguments = $null
$repoRoot = Get-SafeMaintRepoRoot

try {
    Write-Host "[1/7] Docker 설치와 엔진 상태 확인"
    $dockerVersion = Assert-SafeMaintDocker
    Write-Host "      Docker Engine $dockerVersion"

    Write-Host "[2/7] 개발 환경변수 확인"
    $envPath = Resolve-SafeMaintEnvPath -EnvFile $EnvFile
    $envValues = Read-SafeMaintEnvFile -Path $envPath
    Assert-SafeMaintEnvValues -Values $envValues
    $composeArguments = New-SafeMaintComposeArguments -EnvPath $envPath -ProjectName $ProjectName
    $postgresPort = Get-SafeMaintEnvValue -Values $envValues -Name "POSTGRES_PORT" -Default "5432"
    $backendPort = Get-SafeMaintEnvValue -Values $envValues -Name "BACKEND_PORT" -Default "8000"
    $frontendPort = Get-SafeMaintEnvValue -Values $envValues -Name "FRONTEND_PORT" -Default "3000"

    Push-Location $repoRoot
    try {
        Write-Host "[3/7] Docker Compose 구성 검증"
        & docker @composeArguments config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose config 검증에 실패했습니다."
        }

        Write-Host "[4/7] 개발용 DB, migration, seed, backend, RAG, worker, frontend 실행"
        # migrate/seed/backend and rag/worker share images. Building via
        # `up --build` asks BuildKit to build the same image concurrently on
        # Docker Desktop, which can fail with a duplicated gRPC session.
        & docker @composeArguments build backend rag frontend
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose build 실행에 실패했습니다."
        }
        & docker @composeArguments up -d --no-build
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose up 실행에 실패했습니다."
        }

        Write-Host "[5/7] PostgreSQL healthcheck 확인"
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "db" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds

        Write-Host "[6/7] Alembic migration과 seed 종료 코드 확인"
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "migrate" -Expected "completed" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "seed" -Expected "completed" -TimeoutSeconds $TimeoutSeconds

        Write-Host "[7/7] backend, RAG, worker와 frontend 상태 확인"
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "backend" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "rag" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "worker" -Expected "running" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "frontend" -Expected "running" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintHttp -Url "http://127.0.0.1:$backendPort/health/ready" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintHttp -Url "http://127.0.0.1:$frontendPort" -TimeoutSeconds $TimeoutSeconds
    }
    finally {
        Pop-Location
    }

    Write-Host "`nSafeMaint 개발 환경이 정상적으로 실행되었습니다." -ForegroundColor Green
    Write-Host "- Frontend: http://127.0.0.1:$frontendPort"
    Write-Host "- Backend readiness: http://127.0.0.1:$backendPort/health/ready"
    Write-Host "- Local RAG and document worker: running"
    Write-Host "- DBeaver: 127.0.0.1:$postgresPort / DB=$($envValues['POSTGRES_DB']) / User=$($envValues['POSTGRES_USER'])"
    Write-Host "- 상세 검증: .\scripts\verify-db.ps1"
    exit 0
}
catch {
    Write-Host "`nSafeMaint 개발 환경 설정 실패: $($_.Exception.Message)" -ForegroundColor Red
    if ($null -ne $composeArguments) {
        try {
            Show-SafeMaintDiagnostics -ComposeArguments $composeArguments
        }
        catch {
            Write-Host "진단 로그를 가져오지 못했습니다." -ForegroundColor Yellow
        }
    }
    exit 1
}
