# Factor Factory Portable Release

This directory turns Factor Factory into a clean, portable system for a new user.

## What Is Included

- Candidate generation scripts.
- WQ simulation/check/submit loop scripts.
- WQ session watchdog.
- Ledger, retrospective, archive, and dashboard generation.
- Strategy config, schemas, connectors, examples, and tests.
- Public case studies that preserve reusable operating lessons without bundling account/session state.
- Mac and Windows install/run entrypoints.

## What Is Not Included

- Any account cookie, token, or Chrome profile.
- Any previous user's WQ account state.
- Historical `state/audit`, `state/ledger`, `state/visual`, Alpha IDs, submitted Alpha records, or exact winner history.

Each user must start with an empty `state/` directory and their own WQ login session.

## macOS Quick Start

```bash
bash distribution/install_macos.sh
open https://platform.worldquantbrain.com/
python3 distribution/doctor.py
bash distribution/run_macos.sh --dry-run-once
bash distribution/run_macos.sh --background
```

## Windows Quick Start

Run PowerShell from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File distribution/install_windows.ps1
start https://platform.worldquantbrain.com/
python distribution/doctor.py
powershell -ExecutionPolicy Bypass -File distribution/run_windows.ps1 -DryRunOnce
powershell -ExecutionPolicy Bypass -File distribution/run_windows.ps1 -Background
```

## Build A Clean Package

```bash
python3 distribution/build_package.py --output /tmp/factor-factory-clean
```

Then inspect:

```bash
cd /tmp/factor-factory-clean
python3 distribution/doctor.py --skip-live-session
```

## Operating Rule

The system only reuses the local logged-in browser session. It does not copy credentials and does not log in for the user. Keep WQ usage within the platform limits and review official rules before enabling unattended loops.
