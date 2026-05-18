# Factor Factory Distribution Runbook

## Goal

Create and verify a clean Factor Factory release for another user while preserving full system functionality and removing personal state.

## Package Contents

- `config/`
- `connectors/`
- `distribution/`
- `examples/`
- `schemas/`
- `scripts/`
- `tests/`
- `distribution/README.md`
- `docs/factor-factory-distribution-runbook-2026-05-18.md`
- `requirements.txt`

## Excluded Contents

- `state/`
- `state.*`
- `launchd/`
- `__pycache__/`
- `.DS_Store`
- personal account state, Alpha IDs, historical ledgers, audit logs, exact submitted winner history

## Agent Install Checklist

1. Install Python 3.10+.
2. Install dependencies:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
3. Initialize and verify:
   ```bash
   python3 distribution/doctor.py --skip-live-session
   ```
4. Ask the user to open Chrome and log in to WorldQuant BRAIN.
5. Verify live session:
   ```bash
   python3 distribution/doctor.py
   ```
6. Dry-run the loop:
   ```bash
   bash distribution/run_macos.sh --dry-run-once
   ```
   or on Windows:
   ```powershell
   powershell -ExecutionPolicy Bypass -File distribution/run_windows.ps1 -DryRunOnce
   ```
7. Start background loop only after the user confirms:
   ```bash
   bash distribution/run_macos.sh --background
   ```
   or:
   ```powershell
   powershell -ExecutionPolicy Bypass -File distribution/run_windows.ps1 -Background
   ```

## Clean Package Verification

```bash
python3 distribution/build_package.py --output /tmp/factor-factory-clean
cd /tmp/factor-factory-clean
python3 distribution/doctor.py --skip-live-session
python3 -m unittest tests/test_distribution_package.py
```

The package must not contain personal absolute paths or historical state.
