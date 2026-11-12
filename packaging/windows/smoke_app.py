"""Smoke test of the Datakrit Windows app (or of any ``datakrit-ui`` program).

    python packaging/windows/smoke_app.py PROGRAM [--sha256 HEX] [--window] [--timeout SECONDS]

PROGRAM is one of:

- ``Datakrit.exe`` (the one-folder build);
- ``Datakrit-<version>-windows-x64.zip``, unzipped into a temporary folder first;
- ``Datakrit-<version>-windows-x64-setup.exe``: the installer is run silently for the current
  user into a temporary folder, with the Desktop icon. The test checks the installed files,
  the Start Menu and Desktop shortcuts, and the entry under Settings > Apps, runs the checks
  below on the installed app, then runs the uninstaller and checks that the folder, the
  shortcuts, and the entry are gone. It refuses to run where Datakrit is already installed
  for the current user, so that it never touches a real installation;
- a ``datakrit-ui`` program installed by pip.

With ``--sha256`` the zip's or installer's hash is checked first, and a ``.sha256`` file next
to it must be exactly ``<hash>  <name>`` ending in LF. For the app (``Datakrit.exe``, the
zip, the installer) the license notices in ``licenses/`` are checked (``LICENSE_FILES``).

The test then

1. starts the program in smoke mode: ``DATAKRIT_UI_URL_FILE`` names a file, so no window or
   browser opens and the program writes its page address, with the session key, to it;
2. fetches the page with the key and every file of the page;
3. checks that the page is refused without the key or with a wrong key, and that a request
   to the API without the key is refused;
4. presses Quit through the API, and checks that the program exits with code 0.

With ``--window`` (the app only) it also starts the app normally, finds its app window (a
Microsoft Edge or Google Chrome process started by the app with ``--app=<page address>``),
runs the checks above against that address, presses Quit, and checks that the window and
the app end (exit code 0) and that the window's profile is removed; then it starts the app
again and closes the window, which must end the app with exit code 0.

It prints the start-up time (from starting the program until the page was served) and exits
with code 0 when everything passed. Only the standard library (and Windows PowerShell, for
``--window`` and the installer) is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# The files of the page (``STATIC`` in src/datakrit/ui.py), with their content types.
ASSETS = {
    "/app.css": "text/css; charset=utf-8",
    "/app.js": "text/javascript; charset=utf-8",
    "/theme.js": "text/javascript; charset=utf-8",
    "/logo-mark.png": "image/png",
}
TOKEN_HEADER = "X-Datakrit-Token"
# The notices in Datakrit/licenses/ (``LICENSE_FILES`` in build_app.py), each with a text
# that its file must contain.
LICENSE_FILES = {
    "LICENSE": "Permission is hereby granted, free of charge",
    "NOTICE.md": "Copyright (c) 2026 Lucide Icons and Contributors",
    "Python-LICENSE.txt": "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
    "tomli-w-LICENSE.txt": "Permission is hereby granted, free of charge",
    "Microsoft-runtime-NOTICE.txt": "Microsoft Distributable Code",
}
MICROSOFT_DLL = re.compile(
    r"(vcruntime\d+(_\d+)?|msvcp\d+(_\w+)?|concrt\d+|ucrtbase|api-ms-win-[\w.-]+)\.dll",
    re.IGNORECASE,
)
URL_FILE_VARIABLE = "DATAKRIT_UI_URL_FILE"
PAGE_URL = re.compile(r"(http://127\.0\.0\.1:\d+)/\?token=([A-Za-z0-9_-]+)")
WINDOW_PROFILE_PREFIX = "datakrit-window-"
# The installer's AppId (datakrit.iss) names its entry under Settings > Apps.
UNINSTALL_KEY = (
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{9B4D2E39-8AEB-4D82-BEEF-C4C6AAEF7CEE}_is1"
)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SmokeError(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def fetch(url: str, data: bytes | None = None, headers: dict[str, str] | None = None):
    """(status, headers, body); an HTTP error status is returned, not raised."""
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with OPENER.open(request, timeout=30) as answer:
            return answer.status, answer.headers, answer.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def powershell(script: str) -> str:
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return done.stdout


def wait_for(condition, timeout: float, message: str, pause: float = 0.25):
    started = time.perf_counter()
    while True:
        value = condition()
        if value:
            return value
        check(time.perf_counter() - started < timeout, message)
        time.sleep(pause)


# ---------------------------------------------------------------------------- downloads


def check_sha256_file(path: Path, digest: str) -> None:
    """The checksum file next to ``path``, if any, is one LF-terminated line that
    ``sha256sum --check`` reads on every system."""
    checksum = path.with_name(path.name + ".sha256")
    if checksum.exists():
        expected = f"{digest}  {path.name}\n".encode("ascii")
        check(checksum.read_bytes() == expected, f"{checksum.name} is not '<hash>  <name>' + LF")
        print("sha256_file=ok")


def check_download(path: Path, expected_sha256: str | None) -> None:
    if expected_sha256 is None:
        return
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    check(digest == expected_sha256.lower(), f"{path.name}: SHA-256 {digest} differs")
    print(f"sha256=ok {digest}")
    check_sha256_file(path, digest)


def unzip(path: Path, work: Path) -> Path:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        check(names and all(n.startswith("Datakrit/") for n in names), "unexpected zip layout")
        archive.extractall(work / "unzipped")
    return work / "unzipped" / "Datakrit" / "Datakrit.exe"


def check_licenses(folder: Path) -> int:
    """Check ``folder/licenses`` of the app folder ``folder``; return the number of files."""
    licenses = folder / "licenses"
    for name, needed in LICENSE_FILES.items():
        path = licenses / name
        check(path.is_file(), f"licenses/{name} is missing")
        check(needed in path.read_text(encoding="utf-8"), f"licenses/{name} lacks its license")
    notice = (licenses / "Microsoft-runtime-NOTICE.txt").read_text(encoding="utf-8")
    runtime = [
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*.dll")
        if MICROSOFT_DLL.fullmatch(p.name)
    ]
    check(bool(runtime), "the app has no Microsoft runtime files to check")
    missing = [name for name in runtime if f"  {name}\n" not in notice]
    check(not missing, f"the Microsoft notice does not name {missing}")
    extra = sorted(p.name for p in licenses.iterdir()) != sorted(LICENSE_FILES)
    check(not extra, f"licenses/ holds other files: {sorted(p.name for p in licenses.iterdir())}")
    return len(LICENSE_FILES)


# ---------------------------------------------------------------------------- installer


def registered() -> bool:
    """Whether Settings > Apps lists Datakrit for the current user."""
    done = subprocess.run(["reg", "query", UNINSTALL_KEY], capture_output=True, check=False)
    return done.returncode == 0


def shortcuts() -> list[Path]:
    """The Start Menu and Desktop shortcuts that the installer makes."""
    listing = powershell(
        "[Environment]::GetFolderPath('Programs'); [Environment]::GetFolderPath('Desktop')"
    )
    # One folder per line: the folders hold spaces ("Start Menu").
    folders = [line.strip() for line in listing.splitlines() if line.strip()]
    check(len(folders) == 2, "the Start Menu and Desktop folders could not be read")
    return [Path(folder) / "Datakrit.lnk" for folder in folders]


def check_not_installed() -> None:
    check(
        not registered(),
        "Datakrit is already installed for this user; uninstall it first, or run the "
        "installer check on another computer",
    )


def install(setup: Path, target: Path, work: Path, timeout: float) -> Path:
    """Install with ``setup`` into ``target``; return the installed Datakrit.exe."""
    done = subprocess.run(
        [
            str(setup),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/TASKS=desktopicon",
            f"/DIR={target}",
            f"/LOG={work / 'setup.log'}",
        ],
        timeout=timeout,
        check=False,
    )
    check(done.returncode == 0, f"the installer exited with code {done.returncode}")
    check((target / "Datakrit.exe").is_file(), "the installer did not install Datakrit.exe")
    check((target / "unins000.exe").is_file(), "the installer did not add an uninstaller")
    check(registered(), "Settings > Apps does not list Datakrit after the install")
    missing = [str(p) for p in shortcuts() if not p.is_file()]
    check(not missing, f"the installer did not make the shortcuts {missing}")
    print("installed=ok")
    return target / "Datakrit.exe"


def uninstall(folder: Path, timeout: float) -> None:
    uninstaller = folder / "unins000.exe"
    if not uninstaller.is_file():
        check(not registered(), "Settings > Apps lists Datakrit, but it has no uninstaller")
        return
    subprocess.run(
        [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        timeout=timeout,
        check=False,
    )
    # The uninstaller continues from a copy of itself, so wait until everything is gone.
    wait_for(
        lambda: (
            not registered()
            and not (folder / "Datakrit.exe").exists()
            and not any(p.exists() for p in shortcuts())
        ),
        timeout,
        "the uninstaller left the app, its shortcuts, or its entry in Settings > Apps",
        pause=1.0,
    )
    print("uninstalled=ok")


# ---------------------------------------------------------------------------- the page


def check_page(url: str) -> tuple[str, str, str]:
    """Check the page at ``url`` and its files and key; return (base, key, version)."""
    match = PAGE_URL.fullmatch(url)
    check(match is not None, f"unexpected page address {url!r}")
    assert match is not None
    base, token = match.groups()
    status, headers, page = fetch(url)
    check(status == 200, f"the page answered {status}")
    check(headers.get("Content-Type") == "text/html; charset=utf-8", "page content type")
    text = page.decode("utf-8")
    title = re.search(r"<title>Datakrit (\d+\.\d+\.\d+)[^<]*</title>", text)
    check(title is not None, "the page has no Datakrit title with a version")
    assert title is not None
    check("{{" not in text, "the page still holds a template field")
    referenced = {"/" + n for n in re.findall(r'(?:href|src)="([^"#:?/][^"#:?]*)"', text)}
    check(referenced <= set(ASSETS), f"the page uses unknown files {referenced - set(ASSETS)}")
    for name, content_type in ASSETS.items():
        status, headers, body = fetch(base + name)
        check(status == 200 and len(body) > 0, f"{name} answered {status}")
        check(headers.get("Content-Type") == content_type, f"{name} content type")
    for refused in (base + "/", base + "/?token=wrong" + token[:4]):
        status, _, _ = fetch(refused)
        check(status == 403, f"{refused.replace(token, '<key>')} answered {status}, not 403")
    status, _, _ = fetch(base + "/api/ping", b"{}", {"Content-Type": "application/json"})
    check(status == 403, f"the API without the key answered {status}, not 403")
    return base, token, title.group(1)


def press_quit(base: str, token: str) -> None:
    headers = {"Content-Type": "application/json", TOKEN_HEADER: token}
    status, _, body = fetch(base + "/api/quit", b"{}", headers)
    check(status == 200 and json.loads(body) == {"stopped": True}, "Quit was not accepted")


def wait_exit(process: subprocess.Popen, timeout: float, after: str) -> None:
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SmokeError(f"the program did not exit after {after}") from None
    check(code == 0, f"the program exited with code {code} after {after}, not 0")


def start(program: Path, work: Path, name: str, url_file: Path | None):
    temp = work / name
    temp.mkdir()
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    env.pop(URL_FILE_VARIABLE, None)
    if url_file is not None:
        env[URL_FILE_VARIABLE] = str(url_file)
    return subprocess.Popen([str(program)], env=env, cwd=work), temp


def failed_start(process: subprocess.Popen, temp: Path) -> SmokeError:
    error = temp / "datakrit-ui-error.txt"
    detail = error.read_text(encoding="utf-8") if error.exists() else "no error file"
    return SmokeError(f"the program exited with code {process.poll()} before serving: {detail}")


def run(program: Path, work: Path, timeout: float) -> dict[str, object]:
    url_file = work / "url.txt"
    started = time.perf_counter()
    process, temp = start(program, work, "temp", url_file)
    try:
        while not url_file.exists():
            if process.poll() is not None:
                raise failed_start(process, temp)
            check(time.perf_counter() - started < timeout, "no page address within the timeout")
            time.sleep(0.05)
        ready = time.perf_counter() - started
        url = url_file.read_text(encoding="utf-8").strip()
        status, _, _ = fetch(url)
        served = time.perf_counter() - started
        check(status == 200, f"the page answered {status}")
        base, token, version = check_page(url)
        press_quit(base, token)
        wait_exit(process, timeout, "Quit")
        return {
            "version": version,
            "ready_seconds": round(ready, 2),
            "startup_seconds": round(served, 2),
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


# ---------------------------------------------------------------------------- the window


def find_window(temp: Path) -> tuple[int, str] | None:
    """The app window whose profile is in ``temp`` (the app's temporary folder): its browser
    process id and the page address. The profile, not the parent process, identifies it,
    because Edge may start itself again as another process."""
    listing = powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -ne 'powershell.exe' -and "
        "$_.CommandLine -like '*--app=http://127.0.0.1*' } | "
        'ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }'
    )
    for line in listing.splitlines():
        pid, _, command = line.partition("\t")
        found = re.search(r"--app=(http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+)", command)
        if found and pid.strip().isdigit() and str(temp).lower() in command.lower():
            return int(pid), found.group(1)
    return None


def window_running(pid: int) -> bool:
    return bool(powershell(f"Get-Process -Id {pid} -ErrorAction SilentlyContinue").strip())


def open_app_window(program: Path, work: Path, name: str, timeout: float):
    started = time.perf_counter()
    process, temp = start(program, work, name, None)

    def window():
        if process.poll() is not None:
            raise failed_start(process, temp)
        return find_window(temp)

    try:
        pid, url = wait_for(window, timeout, "the app opened no app window within the timeout")
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    return process, temp, pid, url, time.perf_counter() - started


def run_window(program: Path, work: Path, timeout: float) -> dict[str, object]:
    process, temp, pid, url, opened = open_app_window(program, work, "window-quit", timeout)
    try:
        base, token, _ = check_page(url)
        press_quit(base, token)
        wait_exit(process, timeout, "Quit in its window")
        wait_for(lambda: not window_running(pid), timeout, "the window stayed open after Quit")
        wait_for(
            lambda: not list(temp.glob(WINDOW_PROFILE_PREFIX + "*")),
            timeout,
            "the window's profile was not removed",
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    process, temp, pid, url, _ = open_app_window(program, work, "window-close", timeout)
    try:
        check_page(url)
        # A window that ends within appwindow.QUICK_EXIT_SECONDS counts as not opened (the
        # app would then use the default browser), so keep it open longer than that.
        time.sleep(4)
        # Closing the window ends its browser process, as the close button does.
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        wait_exit(process, timeout, "its window was closed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return {"window_seconds": round(opened, 2)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the Datakrit Windows app.")
    parser.add_argument(
        "program", type=Path, help="Datakrit.exe, its zip or installer, or datakrit-ui"
    )
    parser.add_argument("--sha256", help="expected SHA-256 of the zip or the installer")
    parser.add_argument("--window", action="store_true", help="also check the app window")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    path = args.program.resolve()
    is_zip = path.suffix.lower() == ".zip"
    is_setup = path.name.lower().endswith("-setup.exe")
    result: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="datakrit-smoke-") as folder:
        work = Path(folder)
        installed = None
        try:
            if args.sha256 is not None:
                check(is_zip or is_setup, "--sha256 is checked for the zip or installer only")
            check_download(path, args.sha256)
            if is_zip:
                program = unzip(path, work)
            elif is_setup:
                check_not_installed()
                # From here on, whatever the installer put in place is removed again.
                installed = work / "installed"
                program = install(path, installed, work, args.timeout)
            else:
                program = path
            check(program.is_file(), f"{program} does not exist")
            app = program.name.lower() == "datakrit.exe"
            if app:
                result["licenses"] = f"ok {check_licenses(program.parent)} files"
            result.update(run(program, work, args.timeout))
            if args.window:
                check(app, "--window checks the app (Datakrit.exe, its zip, or installer)")
                result.update(run_window(program, work, args.timeout))
            if installed is not None:
                uninstall(installed, args.timeout)
                installed = None
        except SmokeError as error:
            print(f"smoke test failed: {error}", file=sys.stderr)
            return 1
        finally:
            if installed is not None:
                try:
                    uninstall(installed, args.timeout)
                except SmokeError as error:
                    print(f"cleanup failed: {error}", file=sys.stderr)
    for key, value in result.items():
        print(f"{key}={value}")
    print("smoke test passed: page and files served with the key, refused without it, Quit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
