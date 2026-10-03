@echo off
rem 한국어 윈도우 명령 창이 읽는 CP949로 저장한다 (UTF-8이면 한글 줄에서 명령이 어긋난다)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] 처음 실행: 필요한 프로그램을 설치합니다. 몇 분 걸릴 수 있어요.
    python -m venv .venv || goto :nopython
)
call ".venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt || goto :fail

echo [2/2] 한장을 엽니다. 이 창을 닫으면 프로그램이 종료됩니다.
set HANJANG_LOCAL=1
python -m streamlit run app.py --browser.gatherUsageStats false
goto :eof

:nopython
echo 파이썬이 설치되어 있지 않습니다. https://www.python.org/downloads/ 에서 설치해 주세요.
echo 설치 화면에서 "Add python.exe to PATH"를 꼭 체크하세요.
pause
goto :eof

:fail
echo 설치 중 문제가 생겼습니다. 인터넷 연결을 확인한 뒤 다시 실행해 주세요.
pause
