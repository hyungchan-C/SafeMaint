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

function Import-SafeMaintPublicRagPackageIfNeeded {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ComposeArguments,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $statusOutput = & docker @ComposeArguments run --rm backend python -m app.commands.public_rag_package status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "      public RAG 상태 확인 실패; 자동 import를 건너뜁니다." -ForegroundColor Yellow
        Write-Host (($statusOutput | Out-String).Trim()) -ForegroundColor Yellow
        return
    }

    $packages = @()
    try {
        $packages = @($statusOutput | Out-String | ConvertFrom-Json)
    }
    catch {
        Write-Host "      public RAG 상태 응답을 해석하지 못해 자동 import를 건너뜁니다." -ForegroundColor Yellow
        return
    }

    if (@($packages | Where-Object { $_.status -eq "active" }).Count -gt 0) {
        Write-Host "      active public RAG package가 이미 있어 import를 건너뜁니다."
        return
    }

    $packageOutputDir = Join-Path $RepoRoot "safemaint_api_data\safemaint_public_rag_package\output"
    $packagePath = Get-ChildItem -LiteralPath $packageOutputDir -Filter "public_safety_rag_package_*.zip" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $publicKeyPath = Join-Path $packageOutputDir "public_rag_ed25519_public.pem"
    if (
        $null -eq $packagePath -or
        -not (Test-Path -LiteralPath $publicKeyPath -PathType Leaf)
    ) {
        Write-Host "      public RAG package zip/pem이 없어 자동 import를 건너뜁니다." -ForegroundColor Yellow
        Write-Host "      필요 위치: $packageOutputDir" -ForegroundColor Yellow
        return
    }

    Write-Host "      public RAG package를 PostgreSQL에 import합니다."
    $volume = "${packageOutputDir}:/packages:ro"
    $packageName = $packagePath.Name
    $importOutput = & docker @ComposeArguments run --rm --volume $volume backend `
        python -m app.commands.public_rag_package import `
        "/packages/$packageName" `
        --public-key "/packages/public_rag_ed25519_public.pem" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "      public RAG package import 완료"
        return
    }
    $importText = ($importOutput | Out-String)
    if ($importText -match "already installed") {
        Write-Host "      public RAG package가 이미 설치되어 import를 건너뜁니다."
        return
    }
    throw "public RAG package import에 실패했습니다.`n$importText"
}

try {
    Write-Host "[1/8] Docker 설치와 엔진 상태 확인"
    $dockerVersion = Assert-SafeMaintDocker
    Write-Host "      Docker Engine $dockerVersion"

    Write-Host "[2/8] 개발 환경변수 확인"
    $envPath = Resolve-SafeMaintEnvPath -EnvFile $EnvFile
    $envValues = Read-SafeMaintEnvFile -Path $envPath
    Assert-SafeMaintEnvValues -Values $envValues
    $composeArguments = New-SafeMaintComposeArguments -EnvPath $envPath -ProjectName $ProjectName
    $postgresPort = Get-SafeMaintEnvValue -Values $envValues -Name "POSTGRES_PORT" -Default "5432"
    $backendPort = Get-SafeMaintEnvValue -Values $envValues -Name "BACKEND_PORT" -Default "8000"
    $frontendPort = Get-SafeMaintEnvValue -Values $envValues -Name "FRONTEND_PORT" -Default "3000"

    Push-Location $repoRoot
    try {
        Write-Host "[3/8] Docker Compose 구성 검증"
        & docker @composeArguments config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose config 검증에 실패했습니다."
        }

        Write-Host "[4/8] 개발용 DB, migration, seed, backend, RAG, worker, frontend 실행"
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

        Write-Host "[5/8] PostgreSQL healthcheck 확인"
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "db" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds

        Write-Host "[6/8] Alembic migration과 seed 종료 코드 확인"
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "migrate" -Expected "completed" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "seed" -Expected "completed" -TimeoutSeconds $TimeoutSeconds

        Write-Host "[7/8] backend, RAG, worker와 frontend 상태 확인"
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "backend" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "rag" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "worker" -Expected "running" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "frontend" -Expected "running" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintHttp -Url "http://127.0.0.1:$backendPort/health/ready" -TimeoutSeconds $TimeoutSeconds
        $null = Wait-SafeMaintHttp -Url "http://127.0.0.1:$frontendPort" -TimeoutSeconds $TimeoutSeconds

        Write-Host "[8/8] public RAG package 자동 import 확인"
        Import-SafeMaintPublicRagPackageIfNeeded -ComposeArguments $composeArguments -RepoRoot $repoRoot
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
