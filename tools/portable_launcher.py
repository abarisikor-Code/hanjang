"""한장 포터블 실행기 (포터블판의 app/launcher.py로 들어간다 — python/pythonw.exe로 실행해 검은 창이 뜨지 않는다).

1. 한장 서버가 이미 켜져 있으면 그대로 쓰고, 아니면 창 없이 켠다(기록은 %LOCALAPPDATA%/hanjang/server.log).
2. 엣지(없으면 크롬)의 '앱 창'(주소창·탭 없는 창)으로 연다. 한장 전용 프로필을 써서 그 창들을 찾을 수 있게 한다.
3. 서버를 켠 실행기는 한장 창이 모두 닫히면 서버도 끈다. 엣지·크롬이 없으면 기본 브라우저로 열고 서버는 켜 둔다.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import psutil

APP = Path(__file__).resolve().parent
ROOT = APP.parent
PYTHONW = ROOT / "python" / "pythonw.exe"
PORT = 8517  # 다른 Streamlit 앱(기본 8501)과 겹치지 않게
URL = f"http://localhost:{PORT}"
DATA = Path(os.environ.get("LOCALAPPDATA") or ROOT) / "hanjang"
PROFILE = DATA / "window"  # 한장 창 전용 브라우저 프로필
LOG = DATA / "server.log"
NO_WINDOW = 0x08000000


def alive() -> bool:
    try:
        with urllib.request.urlopen(URL + "/_stcore/health", timeout=1) as r:
            return r.read().strip() == b"ok"
    except OSError:
        return False


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "한장", 0x40)


def find_browser() -> str | None:
    bases = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for base in filter(None, bases):
            p = Path(base) / rel
            if p.exists():
                return str(p)
    return None


def windows_open() -> bool:
    """한장 전용 프로필로 열린 브라우저 창(프로세스)이 남아 있는가."""
    key = str(PROFILE).lower()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() in ("msedge.exe", "chrome.exe") and \
                    any(key in (a or "").lower() for a in (p.info["cmdline"] or [])):
                return True
        except (psutil.Error, TypeError):
            continue
    return False


def start_server() -> subprocess.Popen | None:
    env = dict(os.environ, HANJANG_LOCAL="1", PYTHONUTF8="1")
    log = open(LOG, "w", encoding="utf-8")
    server = subprocess.Popen(
        [str(PYTHONW), "-m", "streamlit", "run", "app.py", "--server.headless", "true",
         "--server.port", str(PORT), "--browser.gatherUsageStats", "false"],
        cwd=APP, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    for _ in range(180):  # 처음 켤 때는 컴퓨터에 따라 30초 넘게 걸리기도 한다
        if alive():
            return server
        if server.poll() is not None:
            break
        time.sleep(0.5)
    server.kill()
    message(f"한장을 켜지 못했어요. 컴퓨터를 다시 켠 뒤 한 번 더 해 보세요.\n\n자세한 기록: {LOG}")
    return None


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    server = None
    if not alive():
        server = start_server()
        if server is None:
            return
    browser = find_browser()
    if not browser:  # 앱 창을 못 열면 기본 브라우저로 — 창을 닫았는지 알 수 없으므로 서버는 켜 둔다
        import webbrowser
        webbrowser.open(URL)
        return
    subprocess.Popen([browser, f"--app={URL}", f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", "--window-size=1400,900"])
    if server is None:  # 서버를 먼저 켠 실행기가 창을 지켜보다가 끈다
        return
    time.sleep(10)  # 창이 뜰 때까지
    misses = 0
    while misses < 2:  # 3초씩 두 번 연속 한장 창이 없으면 끈다
        time.sleep(3)
        misses = 0 if windows_open() else misses + 1
    server.terminate()
    try:
        server.wait(10)
    except subprocess.TimeoutExpired:
        server.kill()


if __name__ == "__main__":
    main()
