"""포터블판 만들기: 압축을 풀고 '한장 실행.bat'만 누르면 되는 폴더(파이썬 포함)를 dist/에 만든다.

준비: 파이썬 공식 '임베디드' 파일(예: python-3.12.10-embed-amd64.zip, python.org)을 받아 둔다.
      지금 .venv와 같은 파이썬 버전(3.12)이어야 한다 — 패키지를 .venv의 pip로 그 버전에 맞춰 받기 때문이다.
실행: .venv\\Scripts\\python.exe tools/build_portable.py <임베디드 zip 경로>
결과: dist/한장/ 폴더와 dist/한장_포터블.zip
들어가지 않는 것: .env(키), forms/의 저장한 양식, 직접 저장한 모양 — 받는 사람은 자기 키를 넣는다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
OUT = DIST / "한장"
PRESET_THEMES = ["교과서형", "활동지형", "시험지형", "저학년 놀이형", "노트형"]

# 한국어 윈도우의 명령 창은 실행 파일을 CP949로 읽는다. UTF-8(+chcp 65001)로 쓰면 한글 줄에서 명령이 어긋나 잘린다(실측).
LAUNCHER = r"""@echo off
rem 검은 창 없이 한장 실행기(app\launcher.py)를 켜고 이 창은 바로 닫힌다
start "" "%~dp0python\pythonw.exe" "%~dp0app\launcher.py"
"""

SHORTCUT = r"""@echo off
rem 바탕 화면에 '한장' 바로가기를 만든다 (이 폴더를 옮기면 한 번 더 누르면 된다)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d '한장.lnk')); $s.TargetPath='%~dp0python\pythonw.exe'; $s.Arguments='\"%~dp0app\launcher.py\"'; $s.WorkingDirectory='%~dp0app'; $s.IconLocation='%~dp0app\hanjang.ico'; $s.Description='한장 - 학습지 만들기'; $s.Save()"
if errorlevel 1 (
    echo 바로가기를 만들지 못했어요. '한장 실행'을 더블클릭해서 쓰셔도 됩니다.
) else (
    echo 바탕 화면에 '한장' 바로가기를 만들었어요.
)
pause
"""

GUIDE = r"""한장 — 교육과정에 맞는 A4 학습지 만들기 (포터블판)

1. 이 폴더를 '문서'나 '바탕 화면'처럼 내 폴더에 둡니다. (Program Files 같은 곳은 피하세요)
2. '한장 실행'을 더블클릭합니다. 10~30초 뒤 한장 창이 열려요(처음에는 조금 더 걸려요).
   - 바탕 화면에 아이콘을 두려면 '바탕화면 바로가기 만들기'를 한 번 더블클릭하세요.
   - 처음 실행할 때 윈도우 보안 경고(PC 보호)가 나오면 '추가 정보' → '실행'을 누르세요.
3. 처음에는 Gemini API 키를 넣어야 합니다(무료). 한장 첫 화면의 안내를 따라 하세요.
   - https://aistudio.google.com/apikey 에서 구글 계정으로 로그인 → Create API key → 복사
   - 왼쪽 ⚙️ 설정의 'Gemini API 키' 칸에 붙여 넣기 → '💾 이 컴퓨터에 키 기억하기'를 누르면 다음부터 자동으로 채워져요.
4. 다 쓰면 한장 창을 닫으면 됩니다. 몇 초 뒤 뒤에서 돌던 한장도 저절로 꺼져요.

한장 창은 엣지(인터넷 창)를 빌려 그리지만 주소창 없는 별도 창이라 일반 프로그램처럼 쓰면 됩니다.
키는 각자 자기 것을 쓰세요. 키는 이 컴퓨터의 app\.env 파일에만 저장되며, 이 폴더를 남에게 줄 때는
'기억한 키 지우기'를 먼저 누르거나 app\.env 파일을 지우세요.
만든 학습지는 💾 저장 버튼으로 HTML 파일로 내려받고, 다시 열 때는 처음 화면의 '저장해 둔 학습지 다시 열기'에 올리면 됩니다.
"""


def make_icon(path: Path) -> None:
    """assets/icon.png(분홍 바탕 + 종이 한 장 + 숫자 1)을 윈도우 아이콘(.ico)으로."""
    from PIL import Image

    Image.open(ROOT / "assets" / "icon.png").save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def copy_app(dst: Path) -> None:
    dst.mkdir(parents=True)
    shutil.copy2(ROOT / "app.py", dst / "app.py")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "worksheet_maker", dst / "worksheet_maker", ignore=ignore)
    shutil.copytree(ROOT / "data", dst / "data", ignore=ignore)
    shutil.copytree(ROOT / ".streamlit", dst / ".streamlit")
    shutil.copytree(ROOT / "assets", dst / "assets")
    (dst / "examples").mkdir()
    for p in (ROOT / "examples").iterdir():
        if p.suffix in (".json", ".png"):
            shutil.copy2(p, dst / "examples" / p.name)
    (dst / "themes").mkdir()
    for name in PRESET_THEMES:  # 직접 저장한 모양은 넣지 않는다
        shutil.copy2(ROOT / "themes" / f"{name}.json", dst / "themes" / f"{name}.json")
    (dst / "forms").mkdir()  # 저장한 양식은 넣지 않는다 (개인 자료)
    shutil.copy2(ROOT / "tools" / "portable_launcher.py", dst / "launcher.py")
    make_icon(dst / "hanjang.ico")


def setup_python(embed_zip: Path, dst: Path) -> None:
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(dst)
    pth = next(dst.glob("python3*._pth"))
    ver = pth.stem  # 예: python312
    if ver != f"python{sys.version_info.major}{sys.version_info.minor}":
        raise SystemExit(f"임베디드 파이썬({ver})과 .venv 파이썬({sys.version_info.major}.{sys.version_info.minor}) 버전이 달라요.")
    pth.write_text(f"{ver}.zip\n.\nLib\\site-packages\nimport site\n", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-warn-script-location",
                    "--target", str(dst / "Lib" / "site-packages"), "-r", str(ROOT / "requirements.txt"),
                    "psutil"], check=True)  # psutil: 실행기가 한장 창이 닫혔는지 본다


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    embed_zip = Path(sys.argv[1])
    if OUT.exists():
        shutil.rmtree(OUT)
    print("1/4 앱 파일 복사")
    copy_app(OUT / "app")
    print("2/4 파이썬과 필요한 패키지 넣기 (몇 분 걸려요)")
    setup_python(embed_zip, OUT / "python")
    print("3/4 실행 파일·안내문")
    (OUT / "한장 실행.bat").write_text(LAUNCHER, encoding="cp949", newline="\r\n")
    (OUT / "바탕화면 바로가기 만들기.bat").write_text(SHORTCUT, encoding="cp949", newline="\r\n")
    (OUT / "사용 안내.txt").write_text(GUIDE, encoding="utf-8-sig", newline="\r\n")
    assert not (OUT / "app" / ".env").exists()
    print("4/4 압축")
    zpath = DIST / "한장_포터블.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, Path("한장") / p.relative_to(OUT))
    print(f"완료: {zpath} ({zpath.stat().st_size // 2**20} MB)")


if __name__ == "__main__":
    main()
