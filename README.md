# Factor Factory Portable Release

This directory turns Factor Factory into a clean, portable system for a new user.

## What Is Included

- Candidate generation scripts.
- WQ simulation/check/submit loop scripts.
- WQ session watchdog.
- Ledger, retrospective, archive, and dashboard generation.
- Strategy config, schemas, connectors, examples, and tests.
- Public starter knowledge assets for local candidate generation.
- User-owned WorldQuant official metadata sync tooling.
- Public case studies that preserve reusable operating lessons without bundling account/session state.
- Mac and Windows install/run entrypoints.

## What Is Not Included

- Any account cookie, token, or Chrome profile.
- Any previous user's WQ account state.
- Historical `state/audit`, `state/ledger`, `state/visual`, Alpha IDs, submitted Alpha records, or exact winner history.
- Private course transcripts, OCR dumps, screenshots, or prior user's alpha research artifacts.

Each user must start with an empty `state/` directory and their own WQ login session.

## macOS Quick Start

```bash
bash distribution/install_macos.sh
python3 scripts/run_v15_local_cycle.py --run-id first-local-cycle --candidate-limit 20
python3 distribution/doctor.py --skip-live-session
```

To use live WorldQuant requests, start the browser bridge, open WorldQuant BRAIN, log in with your own account, then run:

```bash
python3 scripts/sync_worldquant_official.py --fields-only
python3 distribution/doctor.py
bash distribution/run_macos.sh --dry-run-once
bash distribution/run_macos.sh --background
```

## Windows Quick Start

Run PowerShell from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File distribution/install_windows.ps1
python scripts/run_v15_local_cycle.py --run-id first-local-cycle --candidate-limit 20
python distribution/doctor.py --skip-live-session
```

To use live WorldQuant requests, start the browser bridge, open WorldQuant BRAIN, log in with your own account, then run:

```powershell
python scripts/sync_worldquant_official.py --fields-only
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

The public bootstrap can generate a local candidate pool without any previous user's state. Live platform evidence, official checks, and submission decisions must come from the user's own account.
