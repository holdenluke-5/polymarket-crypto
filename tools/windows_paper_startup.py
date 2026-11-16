"""Install and control a user-scoped Windows on-logon paper-trading task."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
APP_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "PolymarketCrypto"
INSTALL_ROOT = APP_HOME / "repository"
DATA_ROOT = APP_HOME / "data"
LOG_ROOT = APP_HOME / "logs"
TASK_NAME = "PolymarketCryptoPaper"
PORT = 7860
URL = f"http://127.0.0.1:{PORT}"


def _python_paths(root: Path) -> tuple[Path, Path]:
    scripts = root / ".venv" / "Scripts"
    return scripts / "python.exe", scripts / "pythonw.exe"


def _run(
    command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env or _sanitized_environment(),
        check=True,
        capture_output=True,
        text=True,
    )


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = _run(["git", "-C", str(root), *args], env=env)
    return result.stdout.strip()


def _is_copilot_worktree(root: Path) -> bool:
    return "copilot-worktrees" in {part.lower() for part in root.parts}


def _sanitized_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if (
            key in {"GH_TOKEN", "GH_HOST", "GITHUB_TOKEN"}
            or key.startswith("COPILOT_GH_ACCOUNT_")
            or key.startswith("POLYMARKET_")
        ):
            env.pop(key)
    env.update(
        {
            "BOT_MODE": "paper",
            "BTC_PAPER_AUTOSTART": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "POLYMARKET_PRIVATE_KEY": "",
            "POLYMARKET_FUNDER": "",
            "DATA_DIR": str(DATA_ROOT),
            "DB_PATH": str(DATA_ROOT / "btc_5m_binary_fair_value.db"),
            "DASHBOARD_SERVER_NAME": "127.0.0.1",
            "DASHBOARD_SERVER_PORT": str(PORT),
        }
    )
    return env


def _ensure_durable_repository() -> Path:
    if not _is_copilot_worktree(REPOSITORY_ROOT):
        return REPOSITORY_ROOT

    branch = _git(REPOSITORY_ROOT, "branch", "--show-current")
    remote = _git(REPOSITORY_ROOT, "remote", "get-url", "origin")
    if not branch or not remote:
        raise RuntimeError("Cannot install: the source checkout has no branch or origin.")

    if INSTALL_ROOT.exists():
        installed_remote = _git(INSTALL_ROOT, "remote", "get-url", "origin")
        installed_branch = _git(INSTALL_ROOT, "branch", "--show-current")
        changes = _git(INSTALL_ROOT, "status", "--porcelain")
        if installed_remote != remote or installed_branch != branch:
            raise RuntimeError(
                f"Refusing to replace existing installation at {INSTALL_ROOT}; "
                "its repository or branch differs."
            )
        if changes:
            raise RuntimeError(
                f"Refusing to update modified installation at {INSTALL_ROOT}; "
                "preserve or clean its local changes first."
            )
        _git(INSTALL_ROOT, "pull", "--ff-only", "origin", branch)
    else:
        INSTALL_ROOT.parent.mkdir(parents=True, exist_ok=True)
        git_env = _sanitized_environment()
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        _run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "http.extraheader=",
                "clone",
                "--single-branch",
                "--branch",
                branch,
                remote,
                str(INSTALL_ROOT),
            ],
            env=git_env,
        )
    return INSTALL_ROOT


def _register_task(root: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("The on-logon task installer is supported on Windows only.")
    _python, pythonw = _python_paths(root)
    script = root / "tools" / "windows_paper_startup.py"

    def ps_quote(value: str | Path) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    task_arguments = f'"{script}" run'
    powershell = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$action = New-ScheduledTaskAction -Execute {ps_quote(pythonw)} "
            f"-Argument {ps_quote(task_arguments)} "
            f"-WorkingDirectory {ps_quote(root)}",
            "$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
            "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user",
            "$principal = New-ScheduledTaskPrincipal -UserId $user "
            "-LogonType Interactive -RunLevel Limited",
            "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable "
            "-MultipleInstances IgnoreNew",
            f"Register-ScheduledTask -TaskName {ps_quote(TASK_NAME)} "
            "-Action $action -Trigger $trigger -Principal $principal "
            "-Settings $settings -Force | Out-Null",
        ]
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell],
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            "Could not register the user-level logon task: "
            + (result.stderr.strip() or result.stdout.strip())
        )


def _dashboard_data(timeout: float = 2.0) -> dict[str, Any] | None:
    request = urllib.request.Request(f"{URL}/api/data")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and "runtime" in payload else None


def _start_existing_dashboard(payload: dict[str, Any]) -> None:
    runtime = payload.get("runtime") or {}
    if runtime.get("mode") != "paper":
        raise RuntimeError(
            "A dashboard is already using port 7860 in non-paper mode; "
            "the startup task did not interact with it."
        )
    if str(DATA_ROOT).lower() not in str(payload.get("execution_view", "")).lower():
        raise RuntimeError(
            "Port 7860 belongs to a different dashboard installation; "
            "the startup task did not interact with it."
        )
    if runtime.get("state") != "running":
        request = urllib.request.Request(f"{URL}/api/start", method="POST")
        with urllib.request.urlopen(request, timeout=15.0) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("status") != "running":
            raise RuntimeError(f"Paper loop start failed: {result.get('detail', result)}")


def _has_current_btc_contract(payload: dict[str, Any], now: int | None = None) -> bool:
    html = str(payload.get("execution_view", "")).lower()
    current_time = int(time.time()) if now is None else now
    for match in re.finditer(r"btc-updown-(5m|15m)-(\d+)", html):
        duration = 300 if match.group(1) == "5m" else 900
        start = int(match.group(2))
        current_start = current_time - current_time % duration
        if abs(start - current_start) <= duration:
            return True
    return False


def _run_service(root: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("The Windows startup task can only run on Windows.")
    _python, _pythonw = _python_paths(root)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LOG_ROOT / "dashboard.log"

    payload = _dashboard_data()
    if payload is None:
        env = _sanitized_environment()
        with log_path.open("a", encoding="utf-8") as log_file:
            subprocess.Popen(
                [str(_python), "main.py"],
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

    deadline = time.monotonic() + 120
    payload = None
    started = False
    while time.monotonic() < deadline:
        payload = _dashboard_data()
        if payload is not None:
            runtime = payload.get("runtime") or {}
            if runtime.get("mode") != "paper":
                raise RuntimeError("The dashboard did not start in paper mode.")
            if not started:
                _start_existing_dashboard(payload)
                started = True
            if _has_current_btc_contract(payload):
                break
        time.sleep(2)
    if payload is None or not _has_current_btc_contract(payload):
        raise RuntimeError(
            "The paper dashboard did not display a current BTC 5m/15m contract "
            f"within 120 seconds; inspect {log_path} for details."
        )

    webbrowser.open_new(URL)
    print(f"Paper BTC dashboard is running at {URL}. Logs: {log_path}")


def install() -> None:
    if os.name != "nt":
        raise RuntimeError("The on-logon task installer is supported on Windows only.")
    root = _ensure_durable_repository()
    python, _pythonw = _python_paths(root)
    if not python.is_file():
        _run([sys.executable, "-m", "venv", str(root / ".venv")], cwd=root)
    env = _sanitized_environment()
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            "-e",
            ".",
        ],
        cwd=root,
        env=env,
    )
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    _register_task(root)
    _run_service(root)
    print(
        f"Installed {TASK_NAME} for the current Windows user. "
        f"Repository: {root}; data: {DATA_ROOT}"
    )


def uninstall() -> None:
    if os.name != "nt":
        raise RuntimeError("The on-logon task uninstaller is supported on Windows only.")
    powershell = (
        "$ErrorActionPreference = 'Stop'; "
        f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' "
        "-Confirm:$false -ErrorAction SilentlyContinue"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell],
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            "Could not remove the user-level logon task: "
            + (result.stderr.strip() or result.stdout.strip())
        )
    print(
        f"Removed the {TASK_NAME} logon task. Any dashboard already running "
        "was left open; press Stop in that dashboard to halt paper entries. "
        f"Repository and data were preserved at {APP_HOME}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "run", "uninstall"))
    action = parser.parse_args().action
    if action == "install":
        install()
    elif action == "run":
        _run_service(REPOSITORY_ROOT)
    else:
        uninstall()


if __name__ == "__main__":
    main()
