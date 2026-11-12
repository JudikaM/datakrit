"""The Windows app's own window for the local interface.

The Windows app (a frozen program) shows its page in an app window of Microsoft Edge, or of
Google Chrome when Edge is missing: ``--app=URL`` opens the page without tabs or an address
bar, and a private profile in a new temporary folder keeps the window apart from the user's
own browser and makes it a browser process of its own. Closing that window stops the app,
and when the app stops (Quit, or the idle stop) the window is closed. Without Edge or
Chrome, or when the window does not open, the app opens the page in the default browser, as
before.

The window counts as open while the started browser process runs or while the browser holds
the profile's ``lockfile``. Edge can start itself again as another process (it does so when
the ``__COMPAT_LAYER`` variable is set, which is therefore not passed on); the lock still
shows that window, and closing it then ends the processes that use the profile.

Nothing is installed or downloaded; the browsers that Windows already has are used. Functions
here never print.
"""

from __future__ import annotations

import contextlib
import ntpath
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_PREFIX = "datakrit-window-"
WINDOW_SIZE = "1280,860"
# The file that a Chromium browser keeps locked in its profile folder while it runs.
LOCKFILE = "lockfile"
# A window counts as opened once its lock is seen or its process has run this long. A window
# that is not open by STARTUP_SECONDS did not open; the default browser is used instead.
QUICK_EXIT_SECONDS = 3.0
STARTUP_SECONDS = 15.0
POLL_SECONDS = 0.5
# Profiles left behind by a crash are removed at the next start once they are this old.
STALE_PROFILE_SECONDS = 24 * 60 * 60
# Edge first: Windows 10 and 11 always have it. Each with its folder under Program Files or
# the user's local application data, and its name in the registry's App Paths.
BROWSERS = (
    ("msedge.exe", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("chrome.exe", ("Google", "Chrome", "Application", "chrome.exe")),
)
ROOT_VARIABLES = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
# Windows: start helper programs without a console window (the app has none).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _app_path(name: str) -> str | None:
    """The program that the registry's App Paths lists for ``name`` (Windows only)."""
    if sys.platform != "win32":
        return None
    import winreg

    key_name = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with contextlib.suppress(OSError), winreg.OpenKey(root, key_name) as key:
            value, _ = winreg.QueryValueEx(key, "")
            if isinstance(value, str) and value.strip():
                return value.strip().strip('"')
    return None


def find_browser(
    env: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
    registry: Callable[[str], str | None] = _app_path,
) -> str | None:
    """Edge, else Chrome: from App Paths, then from the usual folders; ``None`` if neither."""
    env = os.environ if env is None else env
    for name, parts in BROWSERS:
        candidates = [registry(name)]
        candidates += [ntpath.join(env[v], *parts) for v in ROOT_VARIABLES if env.get(v)]
        for candidate in candidates:
            if candidate and exists(candidate):
                return candidate
    return None


def window_command(browser: str, url: str, profile: Path) -> list[str]:
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--window-size={WINDOW_SIZE}",
    ]


def window_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for the browser: without ``__COMPAT_LAYER``, which makes Edge start
    itself again as another process."""
    env = os.environ if env is None else env
    return {key: value for key, value in env.items() if key.upper() != "__COMPAT_LAYER"}


def remove_stale_profiles(folder: Path, now: float | None = None) -> None:
    """Remove window profiles in ``folder`` that a crashed run left behind."""
    now = time.time() if now is None else now
    with contextlib.suppress(OSError):
        for path in folder.glob(PROFILE_PREFIX + "*"):
            with contextlib.suppress(OSError):
                if path.is_dir() and now - path.stat().st_mtime > STALE_PROFILE_SECONDS:
                    shutil.rmtree(path, ignore_errors=True)


def profile_processes(profile: Path) -> list[int]:
    """The processes whose command line names ``profile`` (Windows only)."""
    if sys.platform != "win32" or not re.fullmatch(r"[\w.-]+", profile.name):
        return []
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
        f"'*{profile.name}*' -and $_.Name -ne 'powershell.exe' }} | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line) for line in done.stdout.split() if line.isdigit()]


@dataclass
class AppWindow:
    """A started window: its browser process and its private profile folder."""

    process: subprocess.Popen[bytes]
    profile: Path
    opened: float = field(default_factory=time.monotonic)
    find_processes: Callable[[Path], list[int]] = profile_processes

    def locked(self) -> bool:
        """Whether a browser holds the profile's lock, that is, still runs on it."""
        lock = self.profile / LOCKFILE
        try:
            lock.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        return False  # a lock left by an ended browser, now removed

    def wait(self) -> bool:
        """Wait until the window is closed. ``False`` when it never opened."""
        seen = False
        while True:
            running = self.process.poll() is None
            locked = self.locked()
            age = time.monotonic() - self.opened
            seen = seen or locked or (running and age >= QUICK_EXIT_SECONDS)
            if not running and not locked and (seen or age > STARTUP_SECONDS):
                return seen
            time.sleep(POLL_SECONDS)

    def close(self) -> None:
        """Close the window (if it is still open) and remove its profile."""
        if self.process.poll() is None:
            with contextlib.suppress(OSError):
                self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    self.process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=5)
        if self.locked():
            # The browser started itself again as another process: end that one too.
            for pid in self.find_processes(self.profile):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)
        # The browser's helper processes may hold files for a moment after it ends.
        for _ in range(40):
            shutil.rmtree(self.profile, ignore_errors=True)
            if not self.profile.exists():
                return
            time.sleep(0.25)


def open_window(
    url: str,
    find: Callable[[], str | None] = find_browser,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    temp: Path | None = None,
) -> AppWindow | None:
    """Show ``url`` in an app window; ``None`` when no window could be started."""
    browser = find()
    if browser is None:
        return None
    folder = Path(tempfile.gettempdir()) if temp is None else temp
    remove_stale_profiles(folder)
    profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX, dir=folder))
    try:
        process = popen(
            window_command(browser, url, profile),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=window_environment(),
        )
    except OSError:
        shutil.rmtree(profile, ignore_errors=True)
        return None
    return AppWindow(process, profile)
