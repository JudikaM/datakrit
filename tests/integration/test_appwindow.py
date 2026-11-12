"""The Windows app's own window (``datakrit.appwindow``) and how the server follows it.

Browsers are replaced by test doubles: these tests check which program is chosen, the
command that starts the window, and that closing the window stops the server (and the other
way round), without opening a real window. The built app is checked with a real window by
``packaging/windows/smoke_app.py --window``."""

import json
import os
import subprocess
import threading
import time
import urllib.request

import pytest

import datakrit.ui as ui_module
from datakrit import appwindow
from datakrit.ui import TOKEN_HEADER

ENV = {
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "ProgramFiles": r"C:\Program Files",
    "LOCALAPPDATA": r"C:\Users\me\AppData\Local",
}
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CHROME_USER = r"C:\Users\me\AppData\Local\Google\Chrome\Application\chrome.exe"


def no_registry(name):
    return None


def test_edge_is_preferred_and_found_in_the_registry_or_its_folder():
    present = {EDGE, CHROME_USER}
    assert appwindow.find_browser(ENV, present.__contains__, no_registry) == EDGE
    registered = r"D:\Edge\msedge.exe"
    found = appwindow.find_browser(
        ENV, {registered, CHROME_USER}.__contains__, lambda n: registered if "edge" in n else None
    )
    assert found == registered
    # A registry entry for a program that is gone is skipped.
    assert appwindow.find_browser(ENV, {EDGE}.__contains__, lambda n: r"D:\gone.exe") == EDGE


def test_chrome_is_used_without_edge_and_nothing_without_either():
    assert appwindow.find_browser(ENV, {CHROME_USER}.__contains__, no_registry) == CHROME_USER
    assert appwindow.find_browser(ENV, set().__contains__, no_registry) is None
    assert appwindow.find_browser({}, {EDGE}.__contains__, no_registry) is None


def test_the_window_is_an_app_window_with_a_private_profile(tmp_path):
    url = "http://127.0.0.1:8123/?token=abc"
    command = appwindow.window_command(EDGE, url, tmp_path / "p")
    assert command[0] == EDGE
    assert f"--app={url}" in command
    assert f"--user-data-dir={tmp_path / 'p'}" in command
    assert "--no-first-run" in command and "--no-default-browser-check" in command


def test_profiles_left_by_a_crash_are_removed_when_old(tmp_path):
    old, new, other = (tmp_path / n for n in ("datakrit-window-a", "datakrit-window-b", "x"))
    for folder in (old, new, other):
        folder.mkdir()
    day_ago = time.time() - appwindow.STALE_PROFILE_SECONDS - 60
    os.utime(old, (day_ago, day_ago))
    os.utime(other, (day_ago, day_ago))
    appwindow.remove_stale_profiles(tmp_path)
    assert not old.exists() and new.exists() and other.exists()


class FakeBrowser:
    """A browser process: it ends when ``end`` is called (the user closed the window) or when
    it is terminated."""

    def __init__(self):
        self.ended = threading.Event()
        self.terminated = False
        self.command = None

    def poll(self):
        return 0 if self.ended.is_set() else None

    def wait(self, timeout=None):
        if not self.ended.wait(timeout):
            raise subprocess.TimeoutExpired("browser", timeout)
        return 0

    def terminate(self):
        self.terminated = True
        self.ended.set()

    def kill(self):
        self.ended.set()

    def end(self):
        self.ended.set()


def test_open_window_starts_the_browser_and_close_removes_the_profile(monkeypatch, tmp_path):
    browser = FakeBrowser()
    monkeypatch.setenv("__COMPAT_LAYER", "RunAsInvoker")

    def popen(command, **kwargs):
        browser.command = command
        assert kwargs["stdin"] == subprocess.DEVNULL
        # Edge starts itself again as another process when this variable is set.
        assert "__COMPAT_LAYER" not in kwargs["env"] and "PATH" in {
            k.upper() for k in kwargs["env"]
        }
        return browser

    window = appwindow.open_window("http://u", lambda: EDGE, popen, tmp_path)
    assert window is not None and window.profile.is_dir()
    assert window.profile.parent == tmp_path and browser.command[1] == "--app=http://u"
    window.close()
    assert browser.terminated and not window.profile.exists()


def test_no_window_without_a_browser_or_when_it_cannot_start(tmp_path):
    assert appwindow.open_window("http://u", lambda: None, None, tmp_path) is None

    def broken(command, **kwargs):
        raise OSError("cannot start")

    assert appwindow.open_window("http://u", lambda: EDGE, broken, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_a_window_that_started_itself_again_is_followed_by_its_lock(monkeypatch, tmp_path):
    """The started process ends at once, but a browser holds the profile's lock: the window
    is open until the lock is released, and closing it ends the processes on the profile."""
    monkeypatch.setattr(appwindow, "POLL_SECONDS", 0.01)
    browser = FakeBrowser()
    browser.end()
    held = {"lock": True}
    found, killed = [], []
    window = appwindow.AppWindow(
        browser, tmp_path / "p", find_processes=lambda p: found.append(p) or [41, 42]
    )
    window.profile.mkdir()
    monkeypatch.setattr(appwindow.AppWindow, "locked", lambda self: held["lock"])
    monkeypatch.setattr(appwindow.os, "kill", lambda pid, sig: killed.append(pid))
    window.close()
    assert found == [window.profile] and killed == [41, 42]
    threading.Timer(0.2, lambda: held.update(lock=False)).start()
    assert window.wait() is True  # it was open: its lock was seen


def test_the_lock_tells_whether_a_browser_runs_on_the_profile(tmp_path):
    window = appwindow.AppWindow(FakeBrowser(), tmp_path)
    assert window.locked() is False
    (tmp_path / appwindow.LOCKFILE).write_text("", encoding="utf-8")
    assert window.locked() is False and not (tmp_path / appwindow.LOCKFILE).exists()


def test_a_window_that_never_opens_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(appwindow, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(appwindow, "STARTUP_SECONDS", 0.1)
    browser = FakeBrowser()
    browser.end()
    assert appwindow.AppWindow(browser, tmp_path).wait() is False


def test_the_browser_environment_drops_only_the_compatibility_layer():
    env = {"PATH": "x", "__COMPAT_LAYER": "RunAsInvoker", "TEMP": "t"}
    assert appwindow.window_environment(env) == {"PATH": "x", "TEMP": "t"}


# ------------------------------------------------------------------ the server and its window


@pytest.fixture
def served(monkeypatch, tmp_path):
    """Run ``run_ui`` with a fake window in a thread; give the window, the opened browser
    addresses, the page address, and the thread's result list."""
    monkeypatch.setattr(appwindow, "QUICK_EXIT_SECONDS", 0.2)
    monkeypatch.setattr(appwindow, "STARTUP_SECONDS", 0.4)
    monkeypatch.setattr(appwindow, "POLL_SECONDS", 0.02)
    opened_in_browser = []
    monkeypatch.setattr(ui_module.webbrowser, "open", opened_in_browser.append)
    browser = FakeBrowser()
    windows = []

    def window(url):
        made = appwindow.open_window(url, lambda: EDGE, lambda c, **k: browser, tmp_path)
        windows.append(made)
        return made

    url_file = tmp_path / "url.txt"
    codes = []

    def start(window_factory=window):
        thread = threading.Thread(
            target=lambda: codes.append(
                ui_module.run_ui(
                    0, True, quiet=True, url_file=url_file, window=window_factory, idle_minutes=0
                )
            ),
            daemon=True,
        )
        thread.start()
        for _ in range(500):
            if url_file.exists():
                break
            time.sleep(0.01)
        return thread, url_file.read_text(encoding="utf-8").strip()

    return start, browser, windows, opened_in_browser, codes


def quit_page(url):
    base, token = url.split("/?token=")
    request = urllib.request.Request(
        base + "/api/quit",
        data=b"{}",
        headers={"Content-Type": "application/json", TOKEN_HEADER: token},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as answer:
        assert json.loads(answer.read()) == {"stopped": True}


def test_closing_the_window_stops_the_server(served):
    start, browser, windows, opened_in_browser, codes = served
    thread, _url = start()
    assert windows[0] is not None and opened_in_browser == []
    time.sleep(0.3)  # longer than QUICK_EXIT_SECONDS: the window really was open
    browser.end()
    thread.join(10)
    assert not thread.is_alive() and codes == [0]
    assert not windows[0].profile.exists()


def test_quit_on_the_page_closes_the_window(served):
    start, browser, windows, _opened_in_browser, codes = served
    thread, url = start()
    quit_page(url)
    thread.join(10)
    assert not thread.is_alive() and codes == [0]
    assert browser.terminated and not windows[0].profile.exists()


def test_the_default_browser_is_used_without_a_window(served):
    start, _browser, _windows, opened_in_browser, codes = served
    thread, url = start(lambda url: None)
    assert opened_in_browser == [url]
    quit_page(url)
    thread.join(10)
    assert codes == [0]


def test_a_window_that_ends_at_once_falls_back_to_the_browser(served):
    start, browser, _windows, opened_in_browser, codes = served
    browser.end()  # the browser exits right after it was started
    thread, url = start()
    for _ in range(200):
        if opened_in_browser:
            break
        time.sleep(0.01)
    assert opened_in_browser == [url] and thread.is_alive()  # still serving
    quit_page(url)
    thread.join(10)
    assert codes == [0]


def test_only_the_windows_app_opens_its_own_window(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ui_module, "run_ui", lambda *a, **k: calls.append(k) or 0)
    monkeypatch.delenv(ui_module.URL_FILE_VARIABLE, raising=False)
    assert ui_module.gui_main() == 0
    assert "window" not in calls[-1]  # datakrit-ui from pip: the default browser
    monkeypatch.setattr("sys.frozen", True, raising=False)
    assert ui_module.gui_main() == 0
    assert calls[-1]["window"] is appwindow.open_window
    monkeypatch.setenv(ui_module.URL_FILE_VARIABLE, str(tmp_path / "url.txt"))
    assert ui_module.gui_main() == 0
    assert "window" not in calls[-1]  # smoke mode: no window
