<#
Starts / stops / reports the voice engine sidecar (127.0.0.1:17494) with Voicebox's venv interpreter.
Usage: powershell -File scripts/run_engine.ps1 [-Action start|stop|status] [-Variant turbo|turbo-nano] [-Device cpu|cuda]
Model load takes minutes on CPU; poll GET /health until status=ok. Logs: deps/logs/engine.log, engine.err.log. PID: deps/engine.pid.
Clones chatterbox GitHub master (pinned commit) to deps/chatterbox-src on first start: PyPI 0.1.7 has no Nano option.
#>
param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [ValidateSet('turbo', 'turbo-nano')][string]$Variant = $(if ($env:CHATTERBOX_VARIANT) { $env:CHATTERBOX_VARIANT } else { 'turbo' }),
    [ValidateSet('cpu', 'cuda')][string]$Device = $(if ($env:VOICE_ENGINE_DEVICE) { $env:VOICE_ENGINE_DEVICE } else { 'cpu' })
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'deps/voicebox/.venv/Scripts/python.exe'
$pidFile = Join-Path $root 'deps/engine.pid'
$logDir = Join-Path $root 'deps/logs'
$port = if ($env:VOICE_ENGINE_PORT) { $env:VOICE_ENGINE_PORT } else { '17494' }

function Get-EngineHealth {
    try { return Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3 } catch { return $null }
}

switch ($Action) {
    'status' {
        $h = Get-EngineHealth
        if ($h) { Write-Host "engine: UP $($h | ConvertTo-Json -Compress)" } else { Write-Host 'engine: DOWN'; exit 1 }
    }
    'stop' {
        if (Test-Path $pidFile) {
            $p = [int](Get-Content $pidFile)
            Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq $p -or $_.ParentProcessId -eq $p } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Remove-Item $pidFile
            Write-Host "stopped engine (pid $p)"
        } else { Write-Host 'no engine pid file' }
    }
    'start' {
        if (Get-EngineHealth) { Write-Host 'engine already running'; return }
        if (-not (Test-Path $py)) { throw "missing $py; run scripts/setup_deps.ps1 first" }
        $src = Join-Path $root 'deps/chatterbox-src'
        if (-not (Test-Path (Join-Path $src 'src/chatterbox'))) {
            git clone -q https://github.com/resemble-ai/chatterbox $src
            if ($LASTEXITCODE -ne 0) { throw 'git clone chatterbox failed' }
            git -C $src checkout -q 5de7a54aa4e5e2baadb0182dde554908b48b85c2
            if ($LASTEXITCODE -ne 0) { throw 'git checkout pinned chatterbox commit failed' }
        }
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $env:VOICE_ENGINE_PORT = $port; $env:VOICE_ENGINE_DEVICE = $Device; $env:CHATTERBOX_VARIANT = $Variant
        if (-not $env:VOICEBOX_URL) { $env:VOICEBOX_URL = 'http://127.0.0.1:17493' }
        $env:PYTHONUNBUFFERED = '1'
        $proc = Start-Process -FilePath $py -WindowStyle Hidden -PassThru `
            -ArgumentList @("`"$(Join-Path $root 'engine/chatterbox_server.py')`"") `
            -RedirectStandardOutput (Join-Path $logDir 'engine.log') -RedirectStandardError (Join-Path $logDir 'engine.err.log')
        Set-Content -Path $pidFile -Value $proc.Id
        Write-Host "engine starting (pid $($proc.Id), variant $Variant, device $Device); poll http://127.0.0.1:$port/health until status=ok"
    }
}
