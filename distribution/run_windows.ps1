param(
  [switch]$DryRunOnce,
  [switch]$Background
)

$ErrorActionPreference = "Stop"

$Root = if ($env:FACTOR_FACTORY_ROOT) { $env:FACTOR_FACTORY_ROOT } else { Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$Python = if ($env:FACTOR_FACTORY_PYTHON) { $env:FACTOR_FACTORY_PYTHON } else { "python" }
$RunId = if ($env:FACTOR_FACTORY_RUN_ID) { $env:FACTOR_FACTORY_RUN_ID } else { "factor-factory-windows-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") }
$Interval = if ($env:FACTOR_FACTORY_INTERVAL_SECONDS) { $env:FACTOR_FACTORY_INTERVAL_SECONDS } else { "15" }
$MaxRunning = if ($env:FACTOR_FACTORY_MAX_RUNNING) { $env:FACTOR_FACTORY_MAX_RUNNING } else { "3" }
$PendingLimit = if ($env:FACTOR_FACTORY_PENDING_REFRESH_LIMIT) { $env:FACTOR_FACTORY_PENDING_REFRESH_LIMIT } else { "3" }
$WaitingLimit = if ($env:FACTOR_FACTORY_WAITING_REFRESH_LIMIT) { $env:FACTOR_FACTORY_WAITING_REFRESH_LIMIT } else { "5" }
$SubmitLimit = if ($env:FACTOR_FACTORY_SUBMIT_READY_LIMIT) { $env:FACTOR_FACTORY_SUBMIT_READY_LIMIT } else { "4" }
$ProbeLimit = if ($env:FACTOR_FACTORY_PROBE_BATCH_LIMIT) { $env:FACTOR_FACTORY_PROBE_BATCH_LIMIT } else { "3" }
$ProbeCooldown = if ($env:FACTOR_FACTORY_PROBE_RATE_LIMIT_COOLDOWN_SECONDS) { $env:FACTOR_FACTORY_PROBE_RATE_LIMIT_COOLDOWN_SECONDS } else { "180" }
$GlobalCooldown = if ($env:FACTOR_FACTORY_RATE_LIMIT_COOLDOWN_SECONDS) { $env:FACTOR_FACTORY_RATE_LIMIT_COOLDOWN_SECONDS } else { "600" }

Set-Location $Root

$LoopArgs = @(
  "scripts/run_wq_sync_loop.py",
  "--run-id", $RunId,
  "--max-cycles", "0",
  "--interval-seconds", $Interval,
  "--max-running", $MaxRunning,
  "--pending-refresh-limit", $PendingLimit,
  "--waiting-refresh-limit", $WaitingLimit,
  "--submit-ready-limit", $SubmitLimit,
  "--probe-batch-limit", $ProbeLimit,
  "--probe-rate-limit-cooldown-seconds", $ProbeCooldown,
  "--rate-limit-cooldown-seconds", $GlobalCooldown,
  "--auto-replenish"
)

if ($env:WQ_TARGET_ID) {
  $LoopArgs += @("--target-id", $env:WQ_TARGET_ID)
}

if ($DryRunOnce) {
  $LoopArgs[2] = "$RunId-dry-run"
  $LoopArgs[4] = "1"
  $LoopArgs += "--dry-run"
  & $Python @LoopArgs
  exit $LASTEXITCODE
}

if ($Background) {
  New-Item -ItemType Directory -Force -Path "state/logs" | Out-Null
  $OutLog = Join-Path $Root "state/logs/$RunId.out.log"
  $ErrLog = Join-Path $Root "state/logs/$RunId.err.log"
  $Process = Start-Process -FilePath $Python -ArgumentList $LoopArgs -WorkingDirectory $Root -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -PassThru
  $Process.Id | Out-File -Encoding ascii "state/logs/$RunId.pid"
  Write-Host "Started background loop PID $($Process.Id)"
  Write-Host "Stdout: $OutLog"
  Write-Host "Stderr: $ErrLog"
} else {
  & $Python @LoopArgs
}
