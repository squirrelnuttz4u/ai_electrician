<#
=============================================================================
  AI Electrician - Windows installer (PowerShell)

  Checks for Docker Desktop (installs via winget if missing), generates a
  secure .env, then builds and starts the full stack.

  Usage (from the repo root, in an elevated PowerShell):
      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
      .\scripts\install-windows.ps1

  Re-running is safe: an existing .env is never overwritten, so your database
  password and secrets stay stable across upgrades.
=============================================================================
#>
[CmdletBinding()]
param(
    [string]$OllamaUrl = "http://192.168.203.100:11434"
)

$ErrorActionPreference = "Stop"

function Say($m)  { Write-Host "> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!   $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "X   $m" -ForegroundColor Red; exit 1 }

# ---- resolve repo root (script lives in scripts/) -------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "AI Electrician - Windows installer" -ForegroundColor White
Write-Host "Repository: $RepoRoot`n"

# ---- 1. ensure Docker is present + running --------------------------------
function Test-DockerReady {
    try { docker info *> $null; return $LASTEXITCODE -eq 0 } catch { return $false }
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    Ok "Docker CLI found ($(docker --version))."
} else {
    Warn "Docker Desktop not found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Installing Docker Desktop via winget ..."
        winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
        Warn "Docker Desktop was installed. You must:"
        Write-Host "     1. Start 'Docker Desktop' from the Start menu."
        Write-Host "     2. Complete first-run setup (enable the WSL 2 backend if prompted)."
        Write-Host "     3. Wait until the whale icon says 'Docker Desktop is running'."
        Write-Host "     4. Re-run this script.`n"
        exit 0
    } else {
        Die "winget is unavailable. Install Docker Desktop manually from https://www.docker.com/products/docker-desktop/ then re-run this script."
    }
}

Say "Waiting for the Docker engine to be ready ..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    if (Test-DockerReady) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { Die "Docker engine is not running. Start Docker Desktop and wait for it to finish starting, then re-run." }
Ok "Docker engine is running."

# ---- 2. determine compose command -----------------------------------------
docker compose version *> $null
if ($LASTEXITCODE -eq 0) {
    $script:ComposeIsPlugin = $true
    $ComposeLabel = "docker compose"
} elseif (Get-Command docker-compose -ErrorAction SilentlyContinue) {
    $script:ComposeIsPlugin = $false
    $ComposeLabel = "docker-compose"
} else {
    Die "Docker Compose is not available. Update Docker Desktop and re-run."
}
function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)] $ComposeArgs)
    if ($script:ComposeIsPlugin) { & docker compose @ComposeArgs } else { & docker-compose @ComposeArgs }
}
Ok "Using compose command: $ComposeLabel"

# ---- 3. generate .env (only if absent) ------------------------------------
function New-Secret {
    $bytes = New-Object 'System.Byte[]' 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

if (Test-Path ".env") {
    Ok ".env already exists - keeping it (edit it manually to change settings)."
} else {
    Say "Creating .env ..."
    $lines = Get-Content ".env.example"

    $inputUrl = Read-Host "Ollama server URL [$OllamaUrl]"
    if (-not [string]::IsNullOrWhiteSpace($inputUrl)) { $OllamaUrl = $inputUrl }

    $dbPass = New-Secret
    $secret = New-Secret

    $authAns = Read-Host "Require a shared password to open the app (LAN auth)? (y/N)"
    $authEnabled = $authAns -match '^[Yy]'
    $appPass = "changeme"
    if ($authEnabled) {
        $inputPass = Read-Host "Choose an app password [changeme]"
        if (-not [string]::IsNullOrWhiteSpace($inputPass)) { $appPass = $inputPass }
    }

    $out = foreach ($line in $lines) {
        switch -Regex ($line) {
            '^OLLAMA_BASE_URL='      { "OLLAMA_BASE_URL=$OllamaUrl"; continue }
            '^POSTGRES_PASSWORD='    { "POSTGRES_PASSWORD=$dbPass"; continue }
            '^AUTH_SECRET='          { "AUTH_SECRET=$secret"; continue }
            '^AUTH_ENABLED='         { "AUTH_ENABLED=$([string]$authEnabled.ToString().ToLower())"; continue }
            '^AUTH_SHARED_PASSWORD=' { "AUTH_SHARED_PASSWORD=$appPass"; continue }
            default                  { $line }
        }
    }
    # write .env as UTF-8 without BOM (Docker/compose dislike a BOM)
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines((Join-Path $RepoRoot ".env"), $out, $enc)
    Ok ".env created with a random DB password and secret."
}

# ---- helper to read a value from .env -------------------------------------
function Get-EnvValue($key, $default) {
    $m = Select-String -Path ".env" -Pattern "^$key=(.*)$" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value } else { return $default }
}

# ---- 4. build + start ------------------------------------------------------
Say "Building and starting the stack (this can take a few minutes the first time) ..."
Invoke-Compose up -d --build
if ($LASTEXITCODE -ne 0) { Die "docker compose failed to start the stack." }
Ok "Containers started."

$apiPort = Get-EnvValue "API_PORT" "8000"
$webPort = Get-EnvValue "WEB_PORT" "8080"

# ---- 5. wait for API health -----------------------------------------------
Say "Waiting for the API to come up ..."
$apiOk = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$apiPort/api/health" -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $apiOk = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
if ($apiOk) { Ok "API is healthy." } else { Warn "API not healthy yet - check: $ComposeLabel logs api" }

# ---- 6. check Ollama connectivity -----------------------------------------
Say "Checking Ollama connectivity ..."
try {
    $status = Invoke-RestMethod -Uri "http://localhost:$apiPort/api/ollama/status" -TimeoutSec 10
    if ($status.reachable) {
        Ok "Ollama is reachable from the API container."
        $missing = @()
        if ($status.configured) {
            foreach ($k in $status.configured.PSObject.Properties.Name) {
                if (-not $status.configured.$k.present) { $missing += $status.configured.$k.name }
            }
        }
        if ($missing.Count -gt 0) { Warn ("Models not pulled yet: " + ($missing -join ", ") + "  -> run 'ollama pull <name>' on the server.") }
    } else {
        Warn "Ollama is NOT reachable: $($status.error)"
    }
} catch {
    Warn "Could not query Ollama status yet. Details: http://localhost:$apiPort/api/ollama/status"
}

# ---- done ------------------------------------------------------------------
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
       Select-Object -First 1 -ExpandProperty IPAddress)
if (-not $ip) { $ip = "<server-ip>" }

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "  Web app:      http://$ip`:$webPort" -ForegroundColor White
Write-Host "  API / docs:   http://$ip`:$apiPort/docs"
Write-Host "  Ollama check: http://$ip`:$apiPort/api/ollama/status"
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  $ComposeLabel logs -f api worker     # view logs"
Write-Host "  $ComposeLabel restart api worker     # restart services"
Write-Host "  $ComposeLabel down                   # stop the stack"
