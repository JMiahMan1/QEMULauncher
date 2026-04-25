from __future__ import annotations

import argparse
import sys

from .config import AppPaths, ensure_default_profile
from .ui import run_ui
from .vm import ConfigurationError, VMController, shell_join


def _find_profile(profile_id: str | None):
    paths = AppPaths()
    settings, profiles = ensure_default_profile(paths)
    if not profiles:
        raise RuntimeError("No profiles are configured.")
    if profile_id:
        for profile in profiles:
            if profile.profile_id == profile_id or profile.name == profile_id:
                return paths, profile
        raise RuntimeError(f"Profile not found: {profile_id}")
    target_id = settings.last_used_profile or profiles[0].profile_id
    for profile in profiles:
        if profile.profile_id == target_id:
            return paths, profile
    return paths, profiles[0]


def main(argv: list[str] | None = None) -> int:
    # Single instance lock for the UI
    paths = AppPaths()
    lock_file = paths.runtime_dir / "app.lock"

    # Use a file lock to prevent multiple instances
    import fcntl
    try:
        lock_f = open(lock_file, "w")
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        # If we're just doing an integrity check or dry-run, we might allow it,
        # but for safety let's lock everything.
        print("Another instance is already running. Exiting.", file=sys.stderr)
        return 1
    import logging
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(paths.logs_dir / "app.log"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger("qemu-launcher")
    logger.info(f"Application starting... Logs at: {paths.logs_dir / 'app.log'}")

    parser = argparse.ArgumentParser(description="QEMU Launcher")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved QEMU command and exit")
    parser.add_argument("--launch", action="store_true", help="Launch the selected profile without opening the UI")
    parser.add_argument("--profile", help="Profile id or name")
    parser.add_argument("--integrity-check", action="store_true", help="Verify core modules load")
    args = parser.parse_args(argv)

    if args.integrity_check:
        print("[INTEGRITY] Success: All core modules loaded.")
        return 0

    if args.dry_run or args.launch:
        paths, profile = _find_profile(args.profile)
        # Instantiate the controller with correct dependencies
        from .capabilities import probe_qemu
        from .vm import RuntimeArtifacts

        capabilities = probe_qemu(profile.qemu_executable)
        artifacts = RuntimeArtifacts(
            qmp_socket=paths.qmp_socket(profile.name),
            pidfile=paths.pid_file(profile.name),
            log_file=paths.log_file(profile.name),
            stderr_log_file=paths.stderr_log_file(profile.name),
        )
        controller = VMController(profile, capabilities, artifacts)
        try:
            if args.dry_run:
                print(shell_join(controller.preview_command()))
                return 0
            controller.launch()
            return 0
        except (ConfigurationError, OSError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 2

    return run_ui()


if __name__ == "__main__":
    sys.exit(main())
