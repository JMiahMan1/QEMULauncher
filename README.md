# QEMU Launcher

QEMU Launcher is a lightweight desktop controller for QEMU on macOS and Linux. It now uses a typed profile model, a Qt-based settings editor, platform-aware command generation, and a managed runtime layer with QMP sockets, pidfiles, and logs.

## Requirements

- Python 3.12 or newer
- QEMU installed locally on the host

## Current Focus

- Native-feeling settings UI with persistent profiles
- Platform-aware launch commands for macOS and Linux
- Shared-folder support with `virtiofs` when available and `9p` fallback
- Managed runtime state through QMP, logs, and per-profile runtime directories
- CI and local smoke tests that validate real launch behavior instead of mock-only string checks

## Usage

```bash
# Open the launcher UI
python3.12 qemu_app.py

# Preview the current profile command
python3.12 qemu_app.py --dry-run

# Launch the current profile without opening the UI
python3.12 qemu_app.py --launch

# Verify bundled imports
python3.12 qemu_app.py --integrity-check
```

## Testing

```bash
ruff check .
pytest -q
./build.sh ci-test

# Use a specific interpreter for build tooling when needed
PYTHON=python3.13 ./build.sh ci-test
```

## Remote macOS Smoke

For a Mac on your network, use the SSH smoke script:

```bash
# Copy the template once and fill in your local values
cp .env.example .env

# Then run the smoke script; it reads .env automatically
./scripts/remote_macos_smoke.sh
```

The local `.env` file is ignored by git. The tracked `.env.example` file is only a template.

That script fetches the current branch on the remote Mac, requires Python 3.12+, creates an isolated virtualenv, installs dependencies, runs lint/tests, performs the integrity check, and runs the build using that same interpreter. You can point it at a different env file with `ENV_FILE=.env.mac ./scripts/remote_macos_smoke.sh`.

The remote Mac flow is for manual verification outside GitHub Actions so platform-specific issues can be caught before pushing changes into the hosted CI pipeline.
