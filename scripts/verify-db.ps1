[CmdletBinding()]
param(
    [string]$EnvFile = ".env",
    [string]$ProjectName = "",
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dev-common.ps1")

$composeArguments = $null
$env:COMPOSE_IGNORE_ORPHANS = "true"

function Invoke-SafeMaintDbQuery {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Sql
    )

    $output = & docker @script:composeArguments exec -T db psql `
        -X `
        -v ON_ERROR_STOP=1 `
        -U $script:postgresUser `
        -d $script:postgresDatabase `
        -A `
        -t `
        -c $Sql 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL 검증 쿼리에 실패했습니다: $($output -join ' ')"
    }

    return @(
        $output |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

try {
    Write-Host "[1/7] Docker와 환경변수 확인"
    $null = Assert-SafeMaintDocker
    $envPath = Resolve-SafeMaintEnvPath -EnvFile $EnvFile
    $envValues = Read-SafeMaintEnvFile -Path $envPath
    Assert-SafeMaintEnvValues -Values $envValues
    $composeArguments = New-SafeMaintComposeArguments -EnvPath $envPath -ProjectName $ProjectName
    $script:composeArguments = $composeArguments
    $script:postgresUser = [string]$envValues["POSTGRES_USER"]
    $script:postgresDatabase = [string]$envValues["POSTGRES_DB"]

    & docker @composeArguments config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose config 검증에 실패했습니다."
    }

    Write-Host "[2/7] PostgreSQL과 backend 준비 상태 확인"
    $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "db" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds
    $null = Wait-SafeMaintService -ComposeArguments $composeArguments -Service "backend" -Expected "healthy" -TimeoutSeconds $TimeoutSeconds

    Write-Host "[3/7] PostgreSQL 연결과 버전 확인"
    $serverVersionRows = @(Invoke-SafeMaintDbQuery -Sql "SHOW server_version;")
    $identityRows = @(Invoke-SafeMaintDbQuery -Sql "SELECT current_database() || '|' || current_user;")
    $serverVersion = $serverVersionRows[0]
    $identity = $identityRows[0]

    Write-Host "[4/7] pgvector와 Alembic 버전 확인"
    $vectorVersion = @(Invoke-SafeMaintDbQuery -Sql "SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    if ($vectorVersion.Count -ne 1) {
        throw "pgvector 확장이 설치되어 있지 않습니다."
    }
    $alembicVersion = @(Invoke-SafeMaintDbQuery -Sql "SELECT version_num FROM alembic_version;")
    if ($alembicVersion.Count -ne 1) {
        throw "Alembic 현재 버전을 확인할 수 없습니다."
    }
    $headResult = Invoke-SafeMaintNativeCapture -Command {
        & docker @composeArguments exec -T backend python -m alembic -c alembic.ini heads
    }
    if ($headResult.ExitCode -ne 0) {
        throw "Alembic 코드 head를 확인하지 못했습니다: $($headResult.CombinedOutput -join ' ')"
    }
    $alembicHeads = @(
        foreach ($line in $headResult.StandardOutput) {
            $match = [regex]::Match(([string]$line).Trim(), '^(\S+)\s+\(head\)$')
            if ($match.Success) {
                $match.Groups[1].Value
            }
        }
    )
    if ($alembicHeads.Count -ne 1) {
        throw "Alembic head가 하나가 아닙니다: $($alembicHeads -join ', ')"
    }
    if ($alembicVersion[0] -ne $alembicHeads[0]) {
        throw "Alembic이 최신 버전이 아닙니다: DB=$($alembicVersion[0]), code=$($alembicHeads[0])"
    }

    Write-Host "[5/7] 주요 테이블 확인"
    $expectedTables = @(
        "alembic_version",
        "auth_sessions",
        "reference_codes",
        "roles",
        "permissions",
        "role_permissions",
        "sites",
        "user_roles",
        "user_sites",
        "users",
        "equipment",
        "components",
        "assessments",
        "assessment_hazards",
        "checklist_items",
        "documents",
        "document_chunks",
        "document_types",
        "document_versions",
        "document_processing_jobs",
        "public_rag_packages",
        "assessment_evidence",
        "audit_events"
    )
    $tableSql = @"
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('alembic_version', 'auth_sessions', 'reference_codes', 'roles', 'permissions', 'role_permissions', 'sites', 'user_roles', 'user_sites', 'users', 'equipment', 'components', 'assessments', 'assessment_hazards', 'checklist_items', 'documents', 'document_chunks', 'document_types', 'document_versions', 'document_processing_jobs', 'public_rag_packages', 'assessment_evidence', 'audit_events')
ORDER BY tablename;
"@
    $actualTables = @(Invoke-SafeMaintDbQuery -Sql $tableSql)
    $missingTables = @($expectedTables | Where-Object { $actualTables -notcontains $_ })
    if ($missingTables.Count -gt 0) {
        throw "필수 테이블이 없습니다: $($missingTables -join ', ')"
    }

    Write-Host "[6/7] reference_codes와 roles seed 확인"
    $seedCountRows = @(Invoke-SafeMaintDbQuery -Sql "SELECT COUNT(*) FROM reference_codes;")
    $seedCountText = $seedCountRows[0]
    $seedCount = 0
    if (-not [int]::TryParse($seedCountText, [ref]$seedCount) -or $seedCount -le 0) {
        throw "reference_codes seed 데이터가 없습니다."
    }
    $roleSeedRows = @(Invoke-SafeMaintDbQuery -Sql "SELECT COUNT(*) || '|' || string_agg(code, ',' ORDER BY code) FROM roles WHERE is_active AND code IN ('admin', 'document_manager', 'safety_manager', 'worker');")
    if ($roleSeedRows.Count -ne 1 -or $roleSeedRows[0] -ne "4|admin,document_manager,safety_manager,worker") {
        throw "roles seed 데이터가 올바르지 않습니다: $($roleSeedRows -join ', ')"
    }

    Write-Host "[7/7] backend readiness HTTP 확인"
    $backendPort = Get-SafeMaintEnvValue -Values $envValues -Name "BACKEND_PORT" -Default "8000"
    $readinessUrl = "http://127.0.0.1:$backendPort/health/ready"
    $response = Invoke-WebRequest -UseBasicParsing -Uri $readinessUrl -TimeoutSec 10
    if ($response.StatusCode -ne 200) {
        throw "backend readiness가 HTTP $($response.StatusCode)를 반환했습니다."
    }

    Write-Host "`nSafeMaint DB 검증이 완료되었습니다." -ForegroundColor Green
    Write-Host "- PostgreSQL: $serverVersion"
    Write-Host "- 연결: $identity"
    Write-Host "- pgvector: $($vectorVersion[0])"
    Write-Host "- Alembic: $($alembicVersion[0])"
    Write-Host "- 주요 테이블: $($actualTables.Count)/$($expectedTables.Count)"
    Write-Host "- reference_codes: $seedCount rows"
    Write-Host "- roles: admin, document_manager, safety_manager, worker"
    Write-Host "- Backend readiness: HTTP $($response.StatusCode)"
    exit 0
}
catch {
    Write-Host "`nSafeMaint DB 검증 실패: $($_.Exception.Message)" -ForegroundColor Red
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
