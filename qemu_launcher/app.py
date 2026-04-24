from __future__ import annotations

import argparse
import sys

from .config import AppPaths, ensure_default_profile
from .ui import run_ui
from .vm import VMController, shell_join


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
        controller = VMController(paths, profile)
        if args.dry_run:
            print(shell_join(controller.preview_command()))
            return 0
        controller.launch()
        return 0

    return run_ui()


if __name__ == "__main__":
    sys.exit(main())
