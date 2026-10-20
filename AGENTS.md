# 한장 — AI 작업자(Codex 등) 안내

**먼저 `CLAUDE.md`를 끝까지 읽는다.** 개발 원칙·구조·검사 방법이 모두 거기에 있다(이름만 Claude용일 뿐 이 저장소의 공용 개발 문서다).
구조를 바꾸면 `CLAUDE.md`도 같이 고친다. 이 파일은 사용자와 일하는 방식과 새 컴퓨터 준비만 적는다.

## 사용자
- 한국 초등 교사, 개발자가 아니다. 앱은 컴퓨터가 낯선 교사·학부모가 쓴다.
- **사용자에게 보이는 말은 전부 한국어**: 최종 답, 작업 중 진행 설명, 명령 설명까지. 쉬운 말로, 영어 용어는 풀어서.
- 기능은 "보통 선생님·학부모에게 실제로 쓸모 있나"로 판단한다. 쓸모가 애매하면 솔직하게 말한다.
- 가입·로그인·비밀번호·키 입력은 사용자가 직접 한다. 단계별로 안내만 한다.

## 반드시 지킬 것
- **키**: `.env`의 `GEMINI_API_KEY`(앱용), `GEMINI_API_KEY_AUDIT`(대량 점검용, 다른 프로젝트 키)를 화면·로그·커밋에 절대 출력하지 않는다.
  `.env`는 저장소에 없다(사용자가 직접 옮김). 인터넷판 키 칸에 시험 값을 넣지 않는다 — 시험은 내 컴퓨터(localhost)에서 가짜 값으로.
- 대량 생성·점검 스크립트는 `GEMINI_API_KEY_AUDIT`를 먼저 쓴다(무료 한도는 프로젝트별 하루 한도 — 앱 키를 다 쓰면 선생님이 그날 앱을 못 쓴다).
- **정확성**: 학년 교육과정을 벗어난 내용(예: 4학년에 약분)·틀린 정답은 절대 안 된다. 생성 프롬프트·유형·과목을 바꾸면
  여러 학년으로 실제 생성해 문항·정답을 직접 읽어 본 뒤에 끝났다고 말한다.
- 사용자 파일(양식·자료·시험용 사본)을 지우지 않는다. 사용자 수업 자료를 공개 저장소에 올리지 않는다(`forms/*.json`, 직접 저장한 모양은 .gitignore).
- AI 그림 그리기·외부 그림 찾기는 다시 넣지 않는다(무료·무가입 서비스가 없음). 대신 사용자 양식에 문제를 담는 기능을 다듬는다.

## 버전·배포 (사용자가 말할 때만)
- 작업은 `dev` 갈래에 커밋하고, 바뀐 점은 `CHANGELOG.md` 맨 위 '작업 중'에 선생님이 읽는 말로 적는다. `dev` push는 백업이다.
- 인터넷판 https://hanjang.streamlit.app 은 `main`을 Streamlit Cloud가 자동 배포한다. **사용자가 "배포해줘"라고 할 때만**:
  1. CHANGELOG '작업 중' 칸을 `## vX.Y.Z — 날짜`로 바꾸고 새 '작업 중 (다음 버전)\n\n(아직 없음)' 칸을 위에 둔다(사이드바 버전 표시가 첫 `## v`를 읽는다).
  2. dev → main 합치기, `vX.Y.Z` 태그, main·dev·태그 push.
  3. **배포 직후 인터넷판을 실제로 열어 확인**한다(잠들어 있으면 'Yes, get this app back up!'을 눌러 깨운다). 사이드바에
     `한장 vX.Y.Z (made by 백)`이 보이고 오류 상자가 없어야 한다. (v1.3 때 Cloud가 예전 모듈을 쥔 채 app.py만 다시 돌려 ImportError가 났다 — `_fresh_worksheet_modules` 참고)
- 되돌리기는 main을 이전 태그로(사용자 확인 후). 강제 push는 사용자가 분명히 허락할 때만.
- **포터블판은 사용자가 만들라고 할 때만** 만든다: `.venv\Scripts\python.exe tools/build_portable.py dist/python-3.12.10-embed-amd64.zip`
  (임베디드 파이썬 zip은 python.org에서 3.12.x embeddable amd64를 받아 `dist/`에 둔다 — `.venv`와 같은 3.12여야 함). 결과 `dist/한장_포터블.zip`(약 108MB),
  안에 `.env`가 없어야 한다. 윈도우 .bat은 CP949로 쓴다(UTF-8이면 한글 줄에서 명령이 잘린다).

## 새 컴퓨터 준비 (윈도우)
1. Git, Python 3.12(설치 때 'Add python.exe to PATH' 체크)를 설치한다.
2. `git clone https://github.com/abarisikor-Code/hanjang.git` → `cd hanjang` → `git checkout dev`
3. 커밋 작성자(이 저장소만): `git config user.name abarisikor-Code`, `git config user.email 326764217+abarisikor-Code@users.noreply.github.com`
4. `python -m venv .venv` → `.venv\Scripts\python.exe -m pip install -r requirements.txt`
5. `.env`는 사용자가 예전 컴퓨터에서 직접 옮긴다(없으면 `.env.example`을 복사해 사용자가 키를 넣는다).
6. 실행: `run.bat`(내 컴퓨터판, `HANJANG_LOCAL=1`). 인터넷판처럼 시험하려면 `HANJANG_LOCAL` 없이
   `.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502`.
7. 처음 push할 때 GitHub 로그인 창이 뜨면 사용자가 직접 로그인한다.

## 시험
- 문법·불러오기: `python -m py_compile app.py`, `python -c "import worksheet_maker.render, worksheet_maker.docform"`.
  앱 화면은 실제로 켜서 본다(위 6). 조판은 `python -m worksheet_maker.render examples/sample.json --theme themes/활동지형.json`.
- 화면 시험은 헤드리스 엣지/크롬 + DevTools 프로토콜(파이썬 `websockets`)로 했다. 미리보기 틀은 별도 프로세스(OOPIF)라 `Target.setAutoAttach`(flatten)가 필요하다.
  인터넷판은 hanjang.streamlit.app 안의 iframe(`/~/+/`)에 앱이 있다.
- 데이터 검사: `python tools/check_guide.py`. `data/curriculum_elementary.json`은 손으로 고치지 않는다(`tools/build_curriculum.py`).
