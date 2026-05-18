$ErrorActionPreference = "Stop"

$Root = if ($env:FACTOR_FACTORY_ROOT) { $env:FACTOR_FACTORY_ROOT } else { Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$Python = if ($env:FACTOR_FACTORY_PYTHON) { $env:FACTOR_FACTORY_PYTHON } else { "python" }

Set-Location $Root

Write-Host "== Factor Factory Windows install =="
& $Python --version

if (Test-Path "requirements.txt") {
  & $Python -m pip install -r requirements.txt
}

& $Python "distribution/doctor.py" --skip-live-session

Write-Host ""
Write-Host "Install OK. Next:"
Write-Host "1. Bootstrap local candidates: $Python scripts/run_v15_local_cycle.py --run-id first-local-cycle --candidate-limit 20"
Write-Host "2. Optional live sync: start the browser bridge, open Chrome, log in to https://platform.worldquantbrain.com/"
Write-Host "3. Optional official metadata sync: $Python scripts/sync_worldquant_official.py --fields-only"
Write-Host "4. Live doctor: $Python distribution/doctor.py"
Write-Host "5. Start dry run: powershell -ExecutionPolicy Bypass -File distribution/run_windows.ps1 -DryRunOnce"
