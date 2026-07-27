[CmdletBinding()]
param(
    [string]$OutputPath = "artifacts\SafeMaint_Qwen_Colab.zip"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$sourceDir = Join-Path $repoRoot "ai\qwen_service"
$resolvedOutput = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath
} else {
    Join-Path $repoRoot $OutputPath
}
$outputDir = Split-Path -Parent $resolvedOutput

if (-not (Test-Path -LiteralPath $sourceDir)) {
    throw "Qwen service source directory not found: $sourceDir"
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
if (Test-Path -LiteralPath $resolvedOutput) {
    Remove-Item -LiteralPath $resolvedOutput -Force
}

$sourceFiles = Get-ChildItem -LiteralPath $sourceDir -File |
    Where-Object { $_.Extension -notin @(".pyc", ".pyo") }
if (-not $sourceFiles) {
    throw "Qwen service source files were not found: $sourceDir"
}

Add-Type -AssemblyName System.IO.Compression
$archiveStream = [System.IO.File]::Open(
    $resolvedOutput,
    [System.IO.FileMode]::Create
)
$archive = [System.IO.Compression.ZipArchive]::new(
    $archiveStream,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($sourceFile in $sourceFiles) {
        $entry = $archive.CreateEntry(
            "ai/qwen_service/$($sourceFile.Name)",
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        $entryStream = $entry.Open()
        $sourceStream = $sourceFile.OpenRead()
        try {
            $sourceStream.CopyTo($entryStream)
        }
        finally {
            $sourceStream.Dispose()
            $entryStream.Dispose()
        }
    }
}
finally {
    $archive.Dispose()
    $archiveStream.Dispose()
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedOutput).Hash
Write-Host "Created: $resolvedOutput"
Write-Host "SHA256: $hash"
