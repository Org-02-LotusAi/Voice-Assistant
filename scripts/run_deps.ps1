<#
Starts / stops / reports the dependency stack: LiveKit dev server (Docker) + Voicebox headless backend.
Usage: powershell -File scripts/run_deps.ps1 [-Action start|stop|status] [-Warm] [-DataDir <path>]
  start  (default) docker compose up -d, start Voicebox detached on 127.0.0.1:17493, wait for /health
  -Warm  also POST /models/load chatterbox-turbo (first run downloads ~3.9 GB, then minutes to load on CPU)
  stop   stop Voicebox (PID file deps/voicebox.pid) and `docker compose down`
Voicebox data dir default: deps/voicebox-store. It MUST NOT have a path component named "data":
Voicebox rewrites stored paths by stripping everything up to the first "data" component (see docs/DEPENDENCIES.md).
Logs: deps/logs/voicebox.log (stdout), deps/logs/voicebox.err.log (stderr).
#>
param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [switch]$Warm,
    [string]$DataDir = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$vb = Join-Path $root 'deps/voicebox'
$py = Join-Path $vb '.venv/Scripts/python.exe'
$pidFile = Join-Path $root 'deps/voicebox.pid'
$logDir = Join-Path $root 'deps/logs'
$compose = Join-Path $root 'docker-compose.dev.yml'
$port = 17493
if (-not $DataDir) { $DataDir = if ($env:VOICEBOX_DATA_DIR) { $env:VOICEBOX_DATA_DIR } else { Join-Path $root 'deps/voicebox-store' } }
if (($DataDir -split '[\\/]') -contains 'data') {
    throw "Voicebox data dir must not contain a path component named 'data': $DataDir"
}

function Get-VoiceboxHealth {
    try { return Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3 } catch { return $null }
}

switch ($Action) {
    'status' {
        $h = Get-VoiceboxHealth
        if ($h) { Write-Host "voicebox: UP $($h | ConvertTo-Json -Compress)" } else { Write-Host 'voicebox: DOWN' }
        docker ps --filter name=service015-livekit --format 'livekit: {{.Status}} {{.Ports}}'
    }
    'stop' {
        if (Test-Path $pidFile) {
            $p = [int](Get-Content $pidFile)
            Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $p -or $_.ParentProcessId -eq $p } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Remove-Item $pidFile
            Write-Host "stopped voicebox (pid $p)"
        } else { Write-Host 'no voicebox pid file' }
        docker compose -f $compose down
    }
    'start' {
        docker compose -f $compose up -d
        if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed (is Docker Desktop running?)' }
        if (Get-VoiceboxHealth) {
            Write-Host 'voicebox already healthy'
        } else {
            if (-not (Test-Path $py)) { throw "missing $py; run scripts/setup_deps.ps1 first" }
            New-Item -ItemType Directory -Force -Path $logDir, $DataDir | Out-Null
            $proc = Start-Process -FilePath $py -WorkingDirectory $vb -WindowStyle Hidden -PassThru `
                -ArgumentList @('-m', 'backend.main', '--host', '127.0.0.1', '--port', "$port", '--data-dir', "`"$DataDir`"") `
                -RedirectStandardOutput (Join-Path $logDir 'voicebox.log') -RedirectStandardError (Join-Path $logDir 'voicebox.err.log')
            Set-Content -Path $pidFile -Value $proc.Id
            $deadline = (Get-Date).AddSeconds(180)
            while (-not (Get-VoiceboxHealth)) {
                if ($proc.HasExited) { throw "voicebox exited early; see $logDir/voicebox.err.log" }
                if ((Get-Date) -gt $deadline) { throw 'voicebox did not become healthy within 180 s' }
                Start-Sleep -Seconds 2
            }
            Write-Host "voicebox up on 127.0.0.1:$port (pid $($proc.Id), data $DataDir)"
        }
        if ($Warm) {
            Write-Host 'loading chatterbox-turbo (downloads weights on first run)...'
            $sw = [Diagnostics.Stopwatch]::StartNew()
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/models/load" -ContentType 'application/json' `
                -Body '{"model_name":"chatterbox-turbo"}' -TimeoutSec 3600 | Out-Null
            Write-Host ("chatterbox-turbo loaded in {0:N0} s" -f $sw.Elapsed.TotalSeconds)
        }
    }
}
