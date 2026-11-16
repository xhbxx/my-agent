param(
    [string]$Config = "configs/dbgpt-proxy-openai.toml"
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "DATA-AGENT"
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location -LiteralPath $repoPath
$pythonPath = Join-Path $repoPath ".venv/Scripts/python.exe"
$commandPath = Join-Path $repoPath ".venv/Scripts/dbgpt.exe"
if (-not (Test-Path -LiteralPath $commandPath)) {
    throw "DATA-AGENT environment is missing. Install the backend into .venv first."
}

& $pythonPath (Join-Path $PSScriptRoot "setup_demo.py")
if ($LASTEXITCODE -ne 0) { throw "Demo data initialization failed." }
if (-not $env:DATA_AGENT_CONFIG) {
    $env:DATA_AGENT_CONFIG = Join-Path $repoPath "configs/safe-analysis.example.json"
}
if (-not $env:DATA_AGENT_SALES_URL) {
    $demoPath = (Join-Path $repoPath "data/safe-analysis-demo.sqlite3").Replace("\", "/")
    $env:DATA_AGENT_SALES_URL = "sqlite:///$demoPath"
}
if (-not $env:DATA_AGENT_TOKEN) {
    $env:DATA_AGENT_TOKEN = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
}

Write-Host ""
Write-Host "DATA-AGENT Workbench: http://127.0.0.1:5670/safe-analysis/" -ForegroundColor Cyan
Write-Host "Paste this workbench token into the browser (not your model API key):"
Write-Host $env:DATA_AGENT_TOKEN -ForegroundColor Yellow
Write-Host ""
& $commandPath start webserver --config $Config
