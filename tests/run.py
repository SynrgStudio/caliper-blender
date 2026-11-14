# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds the extension, installs it in a throwaway Blender profile and runs tests/in_blender.py.

    python tests/run.py --cli <caliper-nightly-cli or caliper-cli> [--old-cli <one without --markers>] [--blender <blender>]
    python tests/run.py --cli <...> --window <screenshots folder>   (with the window; run it on a desktop nobody uses)

The user's own Blender profile is never touched (BLENDER_USER_RESOURCES points at a temporary folder)."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
default_blender = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" if sys.platform == "win32" else "blender"

parser = argparse.ArgumentParser()
parser.add_argument("--cli", required=True)
parser.add_argument("--old-cli")
parser.add_argument("--blender", default=default_blender)
parser.add_argument("--window", metavar="SHOTS", help="run tests/in_window.py with Blender's window instead; screenshots go to SHOTS")
args = parser.parse_args()

work = Path(tempfile.mkdtemp(prefix="caliper-bridge-test-"))
try:
    env = dict(os.environ, BLENDER_USER_RESOURCES=str(work / "profile"), CALIPER_CLI=str(Path(args.cli).resolve()))
    if args.old_cli:
        env["CALIPER_OLD_CLI"] = str(Path(args.old_cli).resolve())
    dist = work / "dist"
    dist.mkdir()
    subprocess.run([args.blender, "--factory-startup", "--command", "extension", "build",
                    "--source-dir", str(root / "caliper_bridge"), "--output-dir", str(dist)], check=True, env=env)
    package = next(dist.glob("*.zip"))
    subprocess.run([args.blender, "--factory-startup", "--command", "extension", "install-file",
                    "-r", "user_default", "-e", str(package)], check=True, env=env)
    if args.window:
        os.makedirs(args.window, exist_ok=True)
        env["CALIPER_SHOTS"] = str(Path(args.window).resolve())
        # Opening a .blend skips the splash screen.
        blend = work / "empty.blend"
        subprocess.run([args.blender, "-b", "--python-expr", "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % str(blend)], check=True, env=env)
        code = subprocess.run([args.blender, str(blend), "--python-exit-code", "1", "--python", str(root / "tests" / "in_window.py")], env=env, timeout=600).returncode
    else:
        code = subprocess.run([args.blender, "-b", "--python-exit-code", "1", "--python", str(root / "tests" / "in_blender.py")], env=env).returncode
finally:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(code)
