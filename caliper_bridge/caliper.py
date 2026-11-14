# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding Caliper Nightly's command line and running it. No bpy here: the work runs in a thread."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

NIGHTLY_APP_ID = "{031082AF-6809-4C0E-8F0F-510BD50C3ABA}"
CLI_NAME = "caliper-nightly-cli.exe" if sys.platform == "win32" else "caliper-nightly-cli"
ACTIVATE_PAGE = "https://activatecal.synrgs.tech/start"

# The command line's exit codes (docs/CLI.md).
EXIT_TEXT = {
    2: "Caliper did not accept the command",
    3: "Caliper could not read the exported model",
    4: "Caliper ran out of memory",
    5: "Caliper reached its time limit",
    6: "The repair failed",
    9: "Caliper Nightly is not activated",
}


def _windows_install_folders():
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\%s_is1" % NIGHTLY_APP_ID
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, key) as k:
                folder, _ = winreg.QueryValueEx(k, "InstallLocation")
                if folder:
                    yield folder
        except OSError:
            pass
    yield os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "CaliperNightly")


def _linux_install_folders():
    # install.sh writes a menu entry whose Exec is the window beside the command line.
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    folder = os.path.join(share, "applications")
    try:
        names = sorted(n for n in os.listdir(folder) if "caliper-nightly" in n and n.endswith(".desktop"))
    except OSError:
        names = []
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                for line in f:
                    m = re.match(r'Exec="?([^"]+?)"?\s', line)
                    if m:
                        yield os.path.dirname(m.group(1))
        except OSError:
            pass


def find_cli(override=""):
    """The command line to run: the one the preferences name, else the installed Caliper Nightly's."""
    if override:
        path = os.path.abspath(bpy_path(override))
        if os.path.isdir(path):
            path = os.path.join(path, CLI_NAME)
        return path if os.path.isfile(path) else None
    folders = _windows_install_folders() if sys.platform == "win32" else _linux_install_folders()
    for folder in folders:
        path = os.path.join(folder, CLI_NAME)
        if os.path.isfile(path):
            return path
    return shutil.which(CLI_NAME)


def bpy_path(path):
    # Blender's "//" paths are relative to the .blend; resolved by the caller when bpy is there.
    try:
        import bpy
        return bpy.path.abspath(path)
    except ImportError:
        return path


def _hidden():
    if sys.platform == "win32":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": info, "creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def run(cli, args, on_line=None, should_stop=None):
    """Runs the command line; returns (exit code, JSON document or None, stderr text).
    on_line gets each stderr line as it comes; should_stop() true ends the process."""
    process = subprocess.Popen([cli, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_hidden())
    err_lines, out_chunks = [], []

    def read_err():
        for raw in iter(process.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            err_lines.append(line)
            if on_line:
                on_line(line)

    readers = [threading.Thread(target=lambda: out_chunks.append(process.stdout.read()), daemon=True),
               threading.Thread(target=read_err, daemon=True)]
    for r in readers:
        r.start()
    while process.poll() is None:
        if should_stop and should_stop():
            process.kill()
            break
        time.sleep(0.1)
    code = process.wait()
    for r in readers:
        r.join()
    out = b"".join(out_chunks).decode("utf-8", "replace").strip()
    try:
        document = json.loads(out) if out else None
    except ValueError:
        document = None
    return code, document, "\n".join(err_lines)


def version(cli):
    code, doc, err = run(cli, ["version"])
    return doc if code == 0 and doc else None


def failure(code, stderr):
    """What to tell the user when a run did not give a result."""
    text = EXIT_TEXT.get(code, "Caliper ended with code %d" % code)
    detail = next((l for l in stderr.splitlines() if l.strip() and not l.startswith("Repairing")), "")
    if code == 9:
        return text + ". Activate it in Caliper Nightly (key at %s)." % ACTIVATE_PAGE
    return text + (": " + detail if detail else ".")


def analyze(cli, files, should_stop=None):
    """analyze with markers; a Nightly without --markers (before #437) still gives the counts."""
    code, doc, err = run(cli, ["analyze", *files, "--markers"], should_stop=should_stop)
    markers = True
    if code == 2 and "--markers" in err:
        markers = False
        code, doc, err = run(cli, ["analyze", *files], should_stop=should_stop)
    return code, doc, err, markers


def repair(cli, source, output, keep_cavities, on_percent=None, should_stop=None):
    def line(text):
        m = re.match(r"Repairing (\d+)%", text)
        if m and on_percent:
            on_percent(int(m.group(1)))
    args = ["repair", source, "--output", output, "--overwrite", "--cavities", "keep" if keep_cavities else "remove"]
    return run(cli, args, on_line=line, should_stop=should_stop)
