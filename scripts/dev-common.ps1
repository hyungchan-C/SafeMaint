Set-StrictMode -Version 2.0

$script:SafeMaintRepoRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..")
)

function Get-SafeMaintRepoRoot {
    return $script:SafeMaintRepoRoot
}

function Invoke-SafeMaintNativeCapture {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns native stderr progress messages into
        # ErrorRecord objects. Capture them without treating them as failures;
        # the native exit code remains the source of truth.
        $ErrorActionPreference = "Continue"
        $output = @(& $Command 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        StandardOutput = @(
            $output | Where-Object {
                $_ -isnot [System.Management.Automation.ErrorRecord]
            }
        )
        CombinedOutput = $output
    }
}

function Resolve-SafeMaintEnvPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvFile
    )

    if ([System.IO.Path]::IsPathRooted($EnvFile)) {
        return [System.IO.Path]::GetFullPath($EnvFile)
    }

    return [System.IO.Path]::GetFullPath(
        (Join-Path -Path $script:SafeMaintRepoRoot -ChildPath $EnvFile)
    )
}

function Read-SafeMaintEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw (
            ".env 파일을 찾을 수 없습니다: {0}`n" -f $Path
        ) + "저장소 루트에 .env 파일을 만든 뒤 로컬 비밀번호와 필수 환경변수를 입력하세요."
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }

        if ($trimmed -notmatch '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            continue
        }

        $key = $matches[1]
        $value = $matches[2].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$key] = $value
    }

    return $values
}

function Get-SafeMaintEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Values,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string]$Default = ""
    )

    if ($Values.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace($Values[$Name])) {
        return [string]$Values[$Name]
    }
    return $Default
}

function Assert-SafeMaintEnvValues {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Values
    )

    $required = @(
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "DATABASE_URL"
    )

    foreach ($name in $required) {
        if (-not $Values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($Values[$name])) {
            throw "필수 환경변수가 비어 있습니다: $name"
        }
    }

    $database = [string]$Values["POSTGRES_DB"]
    $username = [string]$Values["POSTGRES_USER"]
    $password = [string]$Values["POSTGRES_PASSWORD"]
    $databaseUrl = [string]$Values["DATABASE_URL"]

    if ($database -notmatch '^[A-Za-z0-9_]+$') {
        throw "POSTGRES_DB는 영문, 숫자, 밑줄만 사용할 수 있습니다."
    }
    if ($username -notmatch '^[A-Za-z0-9_]+$') {
        throw "POSTGRES_USER는 영문, 숫자, 밑줄만 사용할 수 있습니다."
    }
    if ($password -in @("change-me", "change-this-local-password")) {
        throw "POSTGRES_PASSWORD를 예시 값이 아닌 팀원 본인의 로컬 비밀번호로 변경하세요."
    }
    if ($password -notmatch '^[A-Za-z0-9_-]{12,}$') {
        throw "POSTGRES_PASSWORD는 12자 이상의 영문, 숫자, 하이픈, 밑줄만 사용하세요."
    }

    $port = 0
    if (-not [int]::TryParse([string]$Values["POSTGRES_PORT"], [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "POSTGRES_PORT는 1~65535 사이의 숫자여야 합니다."
    }

    $expectedUrl = "postgresql+psycopg://${username}:${password}@db:5432/${database}"
    if ($databaseUrl -cne $expectedUrl) {
        throw "DATABASE_URL의 사용자·비밀번호·DB 이름이 POSTGRES_* 값과 일치하지 않습니다."
    }
}

function Assert-SafeMaintDocker {
    if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "docker 명령을 찾을 수 없습니다. Docker Desktop을 설치한 뒤 다시 실행하세요."
    }

    $serverVersion = & docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($serverVersion | Out-String))) {
        throw "Docker 엔진에 연결할 수 없습니다. Docker Desktop을 실행한 뒤 다시 시도하세요."
    }

    return ($serverVersion | Select-Object -First 1).Trim()
}

function New-SafeMaintComposeArguments {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvPath,
        [string]$ProjectName = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($ProjectName) -and $ProjectName -notmatch '^[a-z0-9][a-z0-9_-]*$') {
        throw "ProjectName은 소문자 영문, 숫자, 하이픈, 밑줄만 사용할 수 있습니다."
    }

    $arguments = @(
        "compose",
        "--env-file", $EnvPath
    )
    if (-not [string]::IsNullOrWhiteSpace($ProjectName)) {
        $arguments += @("-p", $ProjectName)
    }
    $arguments += @(
        "-f", (Join-Path $script:SafeMaintRepoRoot "docker-compose.yml"),
        "-f", (Join-Path $script:SafeMaintRepoRoot "docker-compose.dev.yml")
    )
    return $arguments
}

function Get-SafeMaintContainerState {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ComposeArguments,
        [Parameter(Mandatory = $true)]
        [string]$Service
    )

    $containerId = & docker @ComposeArguments ps -a -q $Service 2>$null | Select-Object -Last 1
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
        return $null
    }

    $rawState = & docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.ExitCode}}' $containerId 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($rawState)) {
        return $null
    }

    $parts = ([string]$rawState).Trim().Split('|')
    return @{
        Id = ([string]$containerId).Trim()
        Status = $parts[0]
        Health = $parts[1]
        ExitCode = [int]$parts[2]
    }
}

function Wait-SafeMaintService {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ComposeArguments,
        [Parameter(Mandatory = $true)]
        [string]$Service,
        [Parameter(Mandatory = $true)]
        [ValidateSet("healthy", "completed", "running")]
        [string]$Expected,
        [int]$TimeoutSeconds = 300
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $state = Get-SafeMaintContainerState -ComposeArguments $ComposeArguments -Service $Service
        if ($null -ne $state) {
            if ($Expected -eq "healthy" -and $state.Health -eq "healthy") {
                return $state
            }
            if ($Expected -eq "completed" -and $state.Status -eq "exited") {
                if ($state.ExitCode -eq 0) {
                    return $state
                }
                throw "$Service 컨테이너가 종료 코드 $($state.ExitCode)로 실패했습니다."
            }
            if ($Expected -eq "running" -and $state.Status -eq "running") {
                return $state
            }
            if ($state.Status -in @("dead", "exited") -and $Expected -ne "completed") {
                throw "$Service 컨테이너가 예상보다 일찍 $($state.Status) 상태가 되었습니다."
            }
        }
        Start-Sleep -Seconds 2
    }

    throw "$Service 서비스가 제한 시간 ${TimeoutSeconds}초 안에 $Expected 상태가 되지 않았습니다."
}

function Wait-SafeMaintHttp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return $response.StatusCode
            }
        }
        catch {
            # The service can refuse connections briefly while its process starts.
        }
        Start-Sleep -Seconds 2
    }

    throw "$Url 주소가 제한 시간 ${TimeoutSeconds}초 안에 응답하지 않았습니다."
}

function Show-SafeMaintDiagnostics {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$ComposeArguments
    )

    Write-Host "`n[Docker Compose 상태]" -ForegroundColor Yellow
    & docker @ComposeArguments ps -a
    Write-Host "`n[최근 로그]" -ForegroundColor Yellow
    & docker @ComposeArguments logs --no-color --tail 80 db migrate seed backend frontend
}
