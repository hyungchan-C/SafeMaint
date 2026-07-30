[CmdletBinding()]
param(
    [string]$EnvFile = ".env",
    [string]$TestDatabase = "safemaint_rag_test",
    [string]$DataDirectory = "전처리된데이터",
    [string]$Model = "BAAI/bge-m3",
    [string]$SourceType = "incident",
    [int]$LimitPerSource = 100,
    [int]$EmbeddingLimit = 500,
    [int]$EmbeddingBatchSize = 16,
    [double]$MinSimilarity = 0.25,
    [switch]$SkipEmbedding,
    [switch]$SkipSearch
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dev-common.ps1")

function Assert-NativeSuccess {
    param([Parameter(Mandatory = $true)][string]$Message)
    if ($LASTEXITCODE -ne 0) {
        throw $Message
    }
}

function Invoke-TestDatabaseQuery {
    param([Parameter(Mandatory = $true)][string]$Sql)
    $output = & docker @script:ComposeArguments exec -T db psql `
        -X -v ON_ERROR_STOP=1 -U $script:PostgresUser -d $script:TestDatabase `
        -A -t -c $Sql 2>&1
    Assert-NativeSuccess "테스트 DB 검증 SQL에 실패했습니다: $($output -join ' ')"
    return @(
        $output |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
}

try {
    if ($TestDatabase -notmatch '^[A-Za-z0-9_]+_test$') {
        throw "TestDatabase는 영문·숫자·밑줄로 구성되고 반드시 _test로 끝나야 합니다."
    }
    if ($LimitPerSource -lt 1 -or $LimitPerSource -gt 100) {
        throw "이번 실험의 LimitPerSource는 1~100만 허용됩니다."
    }
    if ($EmbeddingLimit -lt 1 -or $EmbeddingLimit -gt 500) {
        throw "이번 실험의 EmbeddingLimit은 1~500만 허용됩니다."
    }
    if ($SourceType -notmatch '^[A-Za-z0-9_-]+$') {
        throw "SourceType은 영문, 숫자, 하이픈, 밑줄만 사용할 수 있습니다."
    }

    $repoRoot = Get-SafeMaintRepoRoot
    $envPath = Resolve-SafeMaintEnvPath -EnvFile $EnvFile
    $envValues = Read-SafeMaintEnvFile -Path $envPath
    Assert-SafeMaintEnvValues -Values $envValues
    $null = Assert-SafeMaintDocker
    $script:ComposeArguments = New-SafeMaintComposeArguments -EnvPath $envPath
    $script:PostgresUser = [string]$envValues["POSTGRES_USER"]
    $script:TestDatabase = $TestDatabase

    $backendPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $embeddingPython = Join-Path $repoRoot "ai\.venv-embedding\Scripts\python.exe"
    $resolvedDataDirectory = [System.IO.Path]::GetFullPath(
        (Join-Path $repoRoot $DataDirectory)
    )
    if (-not (Test-Path -LiteralPath $backendPython -PathType Leaf)) {
        throw "백엔드 가상환경이 없습니다: $backendPython"
    }
    if (-not (Test-Path -LiteralPath $resolvedDataDirectory -PathType Container)) {
        throw "전처리 데이터 폴더가 없습니다: $resolvedDataDirectory"
    }
    if ((-not $SkipEmbedding -or -not $SkipSearch) -and
        -not (Test-Path -LiteralPath $embeddingPython -PathType Leaf)) {
        throw (
            "임베딩 가상환경이 없습니다. 먼저 다음을 실행하세요:`n" +
            "python -m venv ai\.venv-embedding`n" +
            ".\ai\.venv-embedding\Scripts\python.exe -m pip install " +
            "-r ai\requirements-embedding.txt"
        )
    }

    Push-Location $repoRoot
    try {
        Write-Host "[1/8] 격리 테스트 DB 확인"
        $databaseLookup = & docker @ComposeArguments exec -T db psql `
            -X -v ON_ERROR_STOP=1 -U $PostgresUser -d postgres -A -t `
            -c "SELECT datname FROM pg_database WHERE datname = '$TestDatabase';" 2>&1
        Assert-NativeSuccess "테스트 DB 존재 여부 확인에 실패했습니다."
        if (($databaseLookup | ForEach-Object { ([string]$_).Trim() }) -notcontains $TestDatabase) {
            & docker @ComposeArguments exec -T db createdb `
                -U $PostgresUser -O $PostgresUser -E UTF8 $TestDatabase
            Assert-NativeSuccess "테스트 DB 생성에 실패했습니다."
            Write-Host "      $TestDatabase 생성 완료"
        }
        else {
            Write-Host "      기존 $TestDatabase 유지"
        }

        $encodedUser = [uri]::EscapeDataString($PostgresUser)
        $encodedPassword = [uri]::EscapeDataString([string]$envValues["POSTGRES_PASSWORD"])
        $postgresPort = [string]$envValues["POSTGRES_PORT"]
        $env:DATABASE_URL = (
            "postgresql+psycopg://${encodedUser}:${encodedPassword}" +
            "@127.0.0.1:${postgresPort}/${TestDatabase}"
        )
        $env:PYTHONPATH = "backend"

        Write-Host "[2/8] Alembic migration 적용"
        & $backendPython -m alembic -c backend\alembic.ini upgrade head
        Assert-NativeSuccess "Alembic migration에 실패했습니다."

        Write-Host "[3/8] 원본 JSONL dry-run"
        & $backendPython -m app.commands.import_accidents `
            --data-dir $resolvedDataDirectory --sources domestic fatal `
            --limit-per-source $LimitPerSource --batch-size 500 --dry-run
        Assert-NativeSuccess "dry-run 검증에 실패했습니다."

        Write-Host "[4/8] 샘플 최초 적재"
        & $backendPython -m app.commands.import_accidents `
            --data-dir $resolvedDataDirectory --sources domestic fatal `
            --limit-per-source $LimitPerSource --batch-size 500
        Assert-NativeSuccess "샘플 적재에 실패했습니다."

        Write-Host "[5/8] 동일 샘플 재적재 멱등성 확인"
        & $backendPython -m app.commands.import_accidents `
            --data-dir $resolvedDataDirectory --sources domestic fatal `
            --limit-per-source $LimitPerSource --batch-size 500
        Assert-NativeSuccess "멱등성 재적재에 실패했습니다."

        if (-not $SkipEmbedding) {
            Write-Host "[6/8] BGE-M3 임베딩"
            & $embeddingPython -m app.commands.embed_document_chunks `
                --source-type $SourceType `
                --model $Model --limit $EmbeddingLimit `
                --batch-size $EmbeddingBatchSize --device auto `
                --cache-dir ai\.model-cache
            Assert-NativeSuccess "임베딩 생성에 실패했습니다."
        }
        else {
            Write-Host "[6/8] 임베딩 건너뜀"
        }

        if (-not $SkipSearch) {
            Write-Host "[7/8] pgvector 코사인 검색"
            & $embeddingPython -m app.commands.search_document_chunks `
                --source-type $SourceType `
                --model $Model --top-k 5 --min-similarity $MinSimilarity `
                --device auto --cache-dir ai\.model-cache
            Assert-NativeSuccess "pgvector 검색에 실패했습니다."
        }
        else {
            Write-Host "[7/8] 검색 건너뜀"
        }

        Write-Host "[8/8] DB 일관성 확인"
        $integrity = @(Invoke-TestDatabaseQuery -Sql @"
SELECT CASE WHEN COUNT(*) = 0 THEN 'ok' ELSE 'invalid' END
FROM (
    SELECT
        COUNT(*) AS ready_count,
        COUNT(*) FILTER (
            WHERE c.embedding IS NULL
               OR c.embedding_model IS NULL
               OR c.embedding_dimension IS DISTINCT FROM vector_dims(c.embedding)
        ) AS invalid_count,
        COUNT(DISTINCT c.embedding_model) AS model_count,
        COUNT(DISTINCT c.embedding_dimension) AS dimension_count
    FROM document_chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE d.source_type = '$SourceType'
      AND c.embedding_status = 'ready'
) summary
WHERE summary.ready_count = 0
   OR summary.invalid_count <> 0
   OR summary.model_count <> 1
   OR summary.dimension_count <> 1;
"@
        )
        if ($integrity.Count -ne 1 -or $integrity[0] -ne "ok") {
            throw "Embedding consistency verification failed."
        }
        Write-Host "`nSafeMaint RAG 샘플 실험이 완료되었습니다." -ForegroundColor Green
        Write-Host "- Database: $TestDatabase"
        Write-Host "- 기존 개발 DB와 Docker 볼륨은 삭제하지 않았습니다."
    }
    finally {
        Pop-Location
    }
    exit 0
}
catch {
    Write-Host "`nSafeMaint RAG 실험 실패: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
