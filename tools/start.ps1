param(
    [switch]$NoBrowser,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Test-Python312 {
    param([string]$Executable, [string[]]$Arguments = @())
    try {
        & $Executable @Arguments -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

# Serialize environment setup when the launcher is double-clicked repeatedly.
$hasher = [System.Security.Cryptography.SHA256]::Create()
$pathHash = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot))).Replace('-', '')
$hasher.Dispose()
$setupLock = New-Object System.Threading.Mutex($false, "Local\HerbSetup-$pathHash")
$lockHeld = $false

try {
    Write-Host 'Checking Python environment...'
    try {
        $lockHeld = $setupLock.WaitOne(90000)
    }
    catch [System.Threading.AbandonedMutexException] {
        $lockHeld = $true
    }
    if (-not $lockHeld) {
        throw 'Another launcher is installing dependencies. Wait for it to finish, then try again.'
    }

    if (-not (Test-Python312 $pythonPath)) {
        $systemPython = $null
        $systemArguments = @()
        foreach ($name in @('py', 'python', 'python3')) {
            $command = Get-Command $name -ErrorAction SilentlyContinue
            if (-not $command) { continue }
            $candidateArguments = @()
            if ($name -eq 'py') { $candidateArguments = @('-3.12') }
            if (Test-Python312 $command.Source $candidateArguments) {
                $systemPython = $command.Source
                $systemArguments = $candidateArguments
                break
            }
        }
        if (-not $systemPython) {
            throw 'Python 3.12 is required. Install it from https://www.python.org/downloads/ and enable Add Python to PATH.'
        }
        Write-Host 'Creating .venv...'
        & $systemPython @systemArguments -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }

    # Check installed versions without a network request on later launches.
    $dependencyCheck = @'
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys
for line in Path('requirements.txt').read_text(encoding='utf-8-sig').splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    name, expected = line.split('==', 1)
    try:
        if version(name) != expected:
            sys.exit(1)
    except PackageNotFoundError:
        sys.exit(1)
'@
    & $pythonPath -X utf8 -c $dependencyCheck
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing dependencies. First launch requires internet access...'
        & $pythonPath -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize pip.' }
        & $pythonPath -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            throw 'Dependency installation failed. Check the network and retry; completed downloads can be reused.'
        }
    }
}
finally {
    if ($lockHeld) { $setupLock.ReleaseMutex() }
    $setupLock.Dispose()
}

if ($CheckOnly) {
    Write-Host 'Python 3.12 and required dependencies are ready.'
    exit 0
}

Write-Host 'Starting Herb QA. Configure model APIs on the Settings page.'
$launcherArguments = @('-X', 'utf8', '-m', 'app.launcher')
if ($NoBrowser) { $launcherArguments += '--no-browser' }
& $pythonPath @launcherArguments
if ($LASTEXITCODE -ne 0) {
    throw 'Startup failed. Check store/server.log for details.'
}
