<#
.SYNOPSIS
  One-command launcher for docsgpt-mini (FastAPI + SQLite, port 8202).

.DESCRIPTION
  Starts the uvicorn server in the foreground.  With -Eval it starts the server
  in the background, waits for the health endpoint, runs eval/eval.py against
  it, then stops the server again (useful for CI-like verification).

.PARAMETER Port
  TCP port, default 8202.

.PARAMETER Eval
  Run the evaluation suite against a temporary server instance and exit.

.PARAMETER Install
  Install requirements into the selected Python interpreter first.

.EXAMPLE
  .\run.ps1
  .\run.ps1 -Eval
  .\run.ps1 -Install
#>
param(
    [int]$Port = 8202,
    [switch]$Eval,
    [switch]$Install,
    [string]$BindAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location $projectRoot

function Resolve-Python {
    $candidates = @(
        (Join-Path $projectRoot "..\.venv\Scripts\python.exe"),
        (Join-Path $projectRoot ".venv\Scripts\python.exe")
    )
    foreach ($candidate in $candidates) {
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Path -LiteralPath $resolved) { return $resolved }
    }
    foreach ($name in @("python", "python3", "py")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    throw "Python not found. Install Python 3.10+ or create ..\.venv first."
}

$python = Resolve-Python
Write-Host "Python : $python"
Write-Host "Project: $projectRoot"

if ($Install) {
    Write-Host "Installing requirements ..."
    & $python -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

function Test-PortFree {
    param([int]$CandidatePort)
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $CandidatePort)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($listener) { $listener.Stop() }
    }
}

function Wait-Health {
    param([string]$Url, [int]$Attempts = 120)
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-RestMethod -Uri $Url -TimeoutSec 2
            if ($response.status -eq "ok") { return $true }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

$baseUrl = "http://${BindAddress}:$Port"

if (-not (Test-PortFree -CandidatePort $Port)) {
    Write-Host "Port $Port is already in use. Stop the other process or use -Port <another port>."
    exit 1
}

if ($Eval) {
    Write-Host "Starting temporary server on $baseUrl for evaluation ..."
    $arguments = @("-m", "uvicorn", "app.main:app", "--host", $BindAddress, "--port", "$Port")
    $server = Start-Process -FilePath $python -ArgumentList $arguments `
        -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden
    try {
        if (-not (Wait-Health -Url "$baseUrl/api/health")) {
            throw "Server did not become healthy on $baseUrl"
        }
        Write-Host "Server is healthy. Running evaluation ..."
        & $python (Join-Path $projectRoot "eval\eval.py") --base-url $baseUrl
        if ($LASTEXITCODE -ne 0) { throw "evaluation failed" }
    }
    finally {
        if ($server -and -not $server.HasExited) {
            Write-Host "Stopping temporary server (pid $($server.Id)) ..."
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        }
    }
    exit 0
}

Write-Host "Starting docsgpt-mini ..."
Write-Host "  UI  : $baseUrl/"
Write-Host "  Docs: $baseUrl/api/docs"
Write-Host "  Stop: Ctrl+C"
& $python -m uvicorn app.main:app --host $BindAddress --port $Port
