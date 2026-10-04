# 한장 (hanjang) — 개발 원칙

교사가 자료를 넣거나 직접 편집하면 **항상 같은 규격의 A4 HTML 학습지**가 나오는 Streamlit 앱.

## 핵심 원칙 (절대 깨지 말 것)
- **AI는 구조와 선택만, 코드는 모양만.** Gemini는 `Worksheet` JSON과 `Theme` 선택지 값만 반환한다.
  AI가 HTML/CSS/색상/크기 값을 만들게 하지 않는다. 참고 학습지의 스타일도 `THEME_OPTIONS` 안에서 고르게 한다.
- 디자인 규칙은 `worksheet_maker/templates/worksheet.css` 한 곳에 둔다. 값은 CSS 변수(학교급 `--base` 등, 테마 `--b1~4` 등),
  모양 차이는 `<body>`의 `hd-* sec-* qn-* tag-*` 클래스로만 만든다.
- 번호(문항 1, 2… / 활동 1, 2… / 선택지 ①②…)는 `render.py`가 붙인다. AI 출력의 번호는 지운다.
- 흑백 레이저·갱지 인쇄 기준: 넓은 회색 면을 피하고 선으로 구분한다. (`shade: light`는 선택 사항)
- 사용자·AI 입력은 전부 Jinja 자동 이스케이프를 거친다. `**굵게**`와 `[[빈칸]]` 외의 마크업은 허용하지 않는다.
  그래서 미리보기를 `st.iframe`에 넣어도 안전하다. 이 전제를 깨는 변경(`|safe` 남용 등)을 하지 않는다.

## 구조
- `worksheet_maker/schema.py` — 구성요소 목록(`BLOCK_TYPES`), Pydantic 모델, Gemini JSON 스키마
- `worksheet_maker/theme.py` — 스타일 선택지(`THEME_OPTIONS`), CSS 변수·클래스 변환, `themes/*.json` 저장·불러오기
- `worksheet_maker/llm.py` — 프롬프트와 Gemini 호출. Interactions API(`client.interactions.create`),
  이미지는 `{"type":"image"}`, PDF는 `{"type":"document"}`로 base64 첨부. 구버전 SDK는 generate_content로 대체
- `worksheet_maker/render.py` — JSON(+테마) → HTML 조판. `python -m worksheet_maker.render examples/sample.json --theme themes/활동지형.json`
- `worksheet_maker/extract.py` — PDF·이미지는 첨부로, DOCX/HWPX/TXT는 텍스트로 준비
- `worksheet_maker/images.py` — 올린 그림을 긴 변 1600px 이하로 줄여 data URI로 만든다. `Block.image`는
  `data:image/(png|jpeg|webp|gif);base64,…`만 통과시킨다(외부 주소·SVG 차단). AI 응답 스키마에는 image·size가 없다.
  대신 AI는 image 블록에 file·page·box([ymin,xmin,ymax,xmax], 0~1000)를 주고, `llm.fill_images`가
  `images.PageSource`로 원본(이미지, 또는 pypdfium2로 렌더링한 PDF 쪽)에서 잘라 넣은 뒤 그 필드를 지운다.
- Gemini 호출은 `llm._generate_with_retry`: 503·연결 오류는 같은 모델로 재시도 후 다음 모델, 429·404는 바로 다음 모델,
  키 오류는 즉시 안내. 사용한 모델은 `AnalyzeResult.model_used`.
- `data/curriculum_elementary.json` — 2022 개정 초등 성취기준(국어·수학 1~6, 영어·사회·과학 3~6, 399개).
  **손으로 고치지 말 것.** `python tools/build_curriculum.py`가 NCIC 원문에서 다시 만들고, 원문 코드 수와 다르면 저장하지 않는다.
  `worksheet_maker/curriculum.py`가 읽는다. 성취기준은 학년군 단위(1-2, 3-4, 5-6)이며 학년·학기 배치는 교과서마다 다르다.
- `data/guide/<과목>.json` — 성취기준별 학년 배치(`grades`, `when`)와 학부모용 쉬운 설명(`easy`). 사람이 작성한 데이터.
  고친 뒤에는 `python tools/check_guide.py`로 검사한다(빠진 코드, 학년군 밖 학년, 과학·사회 단원 규칙 위반을 잡는다).
- `worksheet_maker/kinds.py` — **학습지 유형**(`Worksheet.kind`): standard 기본 / test 단원평가지(2단·점수 칸·배점) /
  drill 연습 문제지(`drill` 블록 격자) / note 개념 정리 노트(`cornell` 블록) / inquiry 탐구·활동지 / game 놀이(`bingo` 블록).
  AI에게는 `kinds.rule()` 짜임새 규칙만 주고, 배치는 `<body>`의 `wt-<유형>` 클래스 CSS가, 배점·연습 칸 수·빙고판은
  `render._extras`가 정한다(빙고판은 문항 정답 + AI가 준 오답을 제목으로 시드한 순서로 섞는다). 유형은 사용자가 고른다(`ss.wkind`).
  모양(테마)과 따로 고르며, 3단계와 완성 화면 '모양 바꾸기'에 예시 미리보기(`kinds.SAMPLES`)가 있다.
- **정확성 3단계** (교사·학부모가 그대로 쓰므로 실수가 없어야 한다):
  1. 학년 범위: `curriculum.limits(grade, subject, chosen)`가 고른 성취기준(범위 안이라고 명시 — 다음 학년 내용과 낱말이
     겹쳐 보여도 쓸 수 있다), 그 학년 범위(`data/guide`의 `scope`), `GRADE_NOTES`(학년별 수의 범위·용어·영어 문장 길이),
     '아직 배우지 않은 내용'(다음 두 학년)을 만들어 생성·검토 프롬프트에 넣는다.
     분수는 `llm.fraction_rule`: 약분·통분은 5학년부터(4학년까지는 4/6을 약분하지 않는다).
  2. 프로그램 검산 `verify.check`: 식이 분명한 계산 문제(연습 문제·빈칸 '식 = [[답]]'·식 하나인 단답형·선택형)를 `Fraction`으로
     다시 계산해 바로잡는다. 비례식 'a : b = c : [[d]]'는 외항의 곱 = 내항의 곱으로 검산하고, '='의 왼쪽이 연산 없는 수 하나거나
     ':'가 붙어 있으면 식으로 보지 않는다. `check_number_words`는 '4050200 → 사백오만 이백' 같은 수 읽기·쓰기를 검산한다.
     `verify.strip_numbers`: AI가 쓴 '1번 문항' 지우기, O/X 진술 끝에 붙은 '(O)'·'[[X]]'·'( [[X]] )'·' O' 지우기(정답 칸이 비면
     그 표시나 해설의 '(1) O:'를 정답으로), 선택형·단답형 글에서 정답과 상관없는 [[말]]은 강조로 보고 **굵게**, 묻는 말이 없는
     안내 문장은 설명 글로. `drop_empty_tables`(생성 뒤처리): 내용 없는 표를 뺀다.
     `missing_sources`('위 대화'인데 대화가 없음 — '대화 태도'는 제외), `answer_shown`(영어 글자·낱말 답이 문제 글이나 바로 위
     그림 카드 이름표에 보임 — '대문자 D의 소문자'는 제외), `figure_issues`(빈 표를 보라는 문항, 수 모형이 정답을 보여 줌,
     선택형 보기 겹침 — 띄어쓰기 문제를 위해 공백 차이는 다른 보기로 본다)는 AI가 놓쳐도 프로그램이 잡는다.
  3. AI 검토 `llm.review_worksheet`: 문항을 다시 풀어 정답이 틀리면 고치고, 범위 밖·애매·자료 없음·그림이 정답을 보여 줌·
     정답이 드러남은 그 문항만(그림이 문제면 앞 도식도) `revise_block`으로 다시 쓴다. 설명 글(개념·읽기·표)은 [설명 101]처럼
     번호를 붙여 보여 주고 틀린 사실(`fact`, 예: '비율 = 비율 × 100')만 바로잡는다. 문항 절반 넘게 '범위 밖'이라 하면 성취기준
     자체를 잘못 본 것이므로 범위 지적을 버린다. 고친 뒤 프로그램 검사를 다시 하고 한 번 더.
     앱은 3단계 설정의 토글(기본 켬)로 `_run` 뒤에 돌리고, 고친 내용은 완성 화면 '🔍 검토하며 바로잡은 것'에 보인다.
  - 2026-10 실측: 11가지(1~6학년·5과목·5유형) 생성에서 검토가 틀린 정답 3개, 보기에 정답 없음 1개, 자료 없는 문항,
    정답이 드러난 문항을 찾아 고쳤다. 오탐을 줄이려고 검토 글에서 빈칸은 '(    )'로 보여 준다.
  - 2026-10 전수 점검(성취기준 × 학년 540장, 검토 끔): 수학 계산 정답은 프로그램 검산으로 거의 맞았고, 남은 실수는 용어·범위,
    빈 표, 그림-문항 불일치, 수 읽기, 비례식 검산 오작동이었다(모두 위 검사로 막음). 국어는 계산 실수 대신 문장 짜임·
    띄어쓰기처럼 정답이 둘이거나 틀린 문법 문항이 몇 장에 하나 꼴 — AI 검토가 맡는다(오류가 있던 6장으로 시험해 모두 잡음).
    영어는 그림 카드 이름표(cat)가 철자 문제(c[[a]]t)의 답을 보여 주는 일이 80장 중 15장, 의문사 의문문 억양을 '올라간다'로 틀린
    일이 4장 — 앞의 것은 answer_shown, 뒤의 것은 `GRADE_NOTES['영어']['억양']`(학년과 상관없는 사실)로 생성·검토에 알려 준다.
    사회·과학은 사실 오류가 드물었다.
  - 대량 생성·점검 스크립트는 `.env`의 `GEMINI_API_KEY_AUDIT`(다른 프로젝트의 키)를 쓴다 — 무료 키 한도는 프로젝트·모델별 하루
    한도라 앱 키(`GEMINI_API_KEY`)를 다 쓰면 선생님이 그날 앱을 못 쓴다. 생성이 실패 기록 없이 멈추면 먼저 429를 의심한다.
- 수식: `render._math`가 AI의 LaTeX(`\frac{a}{b}`, `$…$`, `\times`)와 `2/6`을 쌓은 분수(`.frac`)·기호로 바꾼다.
  프롬프트에는 '분수는 2/6처럼'(schema.component_guide)이라고 쓴다.
- **말로 주문하기**(처음 화면, `app._apply_request`): `llm.understand_request`가 `curriculum.catalog()`(399개 한 줄씩) 안에서
  학년·과목·코드·과제 형태(`separate`=과목별로 나눠서 포함)·남은 바람만 고른다. 앱이 코드를 그 학년군·과목으로 걸러
  `ss.sel`에 넣고 '배울 내용' 화면(s2/f2)으로 보내 확인하게 한다. 남은 바람·과제 형태는 `ss.nl`로 3단계 칸에 미리 채운다.
- 만들기 방식(`ss.mode`): `single` 단일과목형(기본) / `fusion` 과목융합형(`llm.generate_integrated`) / `tools` 자료·직접 만들기.
- 문제 수: `app.question_count`(막대 + 숫자 칸, 1~30, `ss.n_questions`). 만들기 설정 폼 밖에 둔다(폼 안 입력칸은 서로 맞출 수
  없다). 프롬프트는 '꼭 N개' — '안팎'이라고 하면 25개를 부탁해도 16개쯤 만든다(2026-10 실측).
- AI 호출 공통: 기본 모델 `gemini-3.5-flash-lite` + `THINKING="high"`, 요청당 `REQUEST_TIMEOUT` 100초, 붐비면 같은 모델 재시도 없이
  다음 모델로. 끊긴 JSON(`_BrokenOutput`)도 다음 모델로. 화면 진행 표시는 `llm.progress`(ContextVar) → `app.ai_status`.
  응답 스키마에서 내용·정답 칸(title, body, items, answer, solution)은 필수여야 한다 — 선택이면 lite 모델이 통째로 비운다.
- 단일과목형(`app.single_wizard`) → `llm.generate_for_standards`: 성취기준(+해설)으로 새 학습지를 만들고 모든 문항에
  `answer`·`solution`을 채운다. 정답 쪽은 `render(show_answers=True)` → 새 쪽(`.answer-sheet`). 빈칸 채우기 정답은 `[[ ]]`에서 뽑는다.
- `worksheet_maker/diagrams.py` — 도식(diagram 블록). AI는 `diagram_json`(문자열)으로 설계도만 주고 `Block`이 dict로 바꿔
  `normalize`로 범위를 바로잡는다. `render_svg`가 흑백 SVG를 그린다(글자는 escape). 새 도식은 `KINDS`·`_draw_<kind>` 추가.
- Gemini 그림 모델은 쓰지 않는다(무료 키 한도 0, 결제 키 사용자 없음).
- 고른 배울 내용은 체크박스와 따로 `ss.sel["<prefix>|<과목>"]`(코드 목록)에 기억한다(영역을 바꿔도 유지).
  체크박스 `on_change=_toggle_code`, 사이드바 `selection_box`와 만들기 버튼 위에 요약. 단일과목형 여러 내용 → `mix`(separate/combined).
- 고치기 '✨ AI 도움'(`app._ai_help`) → `llm.revise_block`(스키마는 `schema.block_schema`): 추천안을 `ss["<id>_sugg"]`에 두고
  '적용'을 눌러야 바뀐다. 적용 시 그 구성요소의 위젯 키(`<id>_*`)를 지워 입력칸이 새 값으로 다시 그려지게 한다.
- 학습지 모양 고르기는 `render(..., thumbnail=True)`(125mm 좁은 종이) 미리보기를 `st.iframe`으로 보여 준다.
- 그림을 AI로 그리거나 외부에서 찾아오는 기능은 없앴다(무료로 쓸 수 있는 서비스가 없고 가입이 어려움). 그림은 프로그램이 그리는
  도식·그림 카드, 사용자가 올린 그림 파일, 따라 만들기에서 원본을 잘라 온 그림뿐이다. 생성 프롬프트는 image 컴포넌트를 쓰지 않게 한다.
- `worksheet_maker/forms.py` — **내 학습지 양식**. 올린 PDF(앞 4쪽)·사진을 쪽 그림(JPEG, 긴 변 2000px)으로 바꾸고,
  `llm.find_form_regions`가 쪽마다 칸(`title`/`content`, box 0~1000, `erase`)만 찾는다. `forms.snap`이 칸 끝을 테두리 선 안쪽으로
  당긴다(지울 때 테두리가 지워지지 않게). render(form=…)는 `#form-src`에 내용을 숨겨 그린 뒤, HTML 안의 스크립트가
  `template.form-tpl` 쪽을 복제해 칸에 블록을 차례로 담는다(넘치면 다음 칸·쪽, 양식 쪽 수를 넘으면 글자 1→0.93→0.86배를 시도하고
  그래도 넘치면 마지막 쪽 반복, 제목 칸은 쪽마다). 쪽 그림은 `hanjang-data` JSON에서 스크립트가 넣는다(HTML에 한 번만 들어가게).
  인쇄는 `@page form { margin: 0 }`. 앱 상태는 `ss.form`(dict) — `set_form()`으로 바꾼다. 저장해 둔 양식은 `forms/*.json`.
- `worksheet_maker/hwpx.py` — 한글(HWPX) 파일 읽기(AI 없음): 문단·표(칸 위치·합친 칸·크기·테두리·칠·배경 그림)·글자·그림을
  `Doc`으로 꺼내고 원본에 가깝게 HTML로 그린다(`para_html`/`table_html`, 글은 escape, 공백은 pre-wrap). 단위 HWPUNIT=1/7200인치.
  `to_dict`/`from_dict`로 저장하며, `from_dict`가 색(#rrggbb)·그림(data URI)·정렬·선 모양·숫자 범위를 다시 검사한다(저장 파일은
  사용자가 고칠 수 있으므로 — style 속성에 그대로 들어가는 값은 반드시 이 검사를 거친다). `units`는 위에서부터의 조각(문단 하나,
  또는 글이 든 줄 묶음이 셋 이상인 큰 표의 줄 묶음 — rowspan으로 이어진 줄은 함께).
- `worksheet_maker/docform.py` — **한글 양식**(`forms.Form.doc`): 조각을 머리 `[0, head)` / 문제 자리 / 꼬리 `[foot, 끝)`로 나눈다
  (`guess_split`: 이름 칸·'공부할 내용' 이름표가 있는 마지막 조각까지 머리, 첫 '1.' 문제부터는 머리 아님, 끝의 '※'는 꼬리).
  `derive_style`이 문제 자리에서 짜임(lines 답 줄 표 / table 이름표 칸 / boxes 그림 조각 둥근 상자)·글꼴·번호 모양·칸 색·선을 읽어
  `DocStyle`로. `_slots`가 머리의 제목(가장 큰 글씨 묶음의 마지막, 그 앞은 단원)·'공부할 내용' 옆 칸·'단원' 옆 칸을 찾아
  `fill`이 학습지 제목·목표·단원을 넣는다(사용자가 고친 글 `edits`가 먼저). render는 `parts()`로 첫 쪽 틀(머리 + 문제 칸)과
  다음 쪽 틀을 만들고, 그림 양식과 같은 스크립트가 문제를 칸에 흘려 담는다(한글 양식은 글자를 줄이지 않고 쪽을 늘린다).
  모양 차이는 worksheet.css의 `.df-*`·`dn-*`·`dsec-*`와 `--df-*` 변수로만. 앱 편집 화면은 `app._doc_editor`.
  **PDF·사진 양식**(`DocForm.img: ImgSource`): `llm.analyze_form_page`가 첫 쪽에서 위치(head_end·body·foot_end, 0~1000)·
  머리·꼬리의 글 줄(box·text·role)·짜임 선택지(layout·number·section·question_bold·serif)·이름표 칸 위치만 고른다.
  `from_image`가 `_snap_text`로 글 상자를 실제 글자에 맞추고(AI 좌표는 한 줄쯤 어긋남 — 줄·낱말 묶음 가운데 읽은 글의 너비와
  맞는 것), 바탕색·글자색·빈 곳(room)을 재고, `derive_img_style`이 가로선에서 선 색·굵기·답 줄 간격, 이름표 칸에서 칸 색을 잰다.
  그리기는 가로띠(`.img-band`, 배경 그림은 `parts()['css']`로 한 번만): 고친 글·자동 칸만 바탕색으로 덮고 새로 쓴다(`.img-text`,
  글자 크기는 스크립트 `fitText`가 줄인다). 첫 쪽만 쓰고 다음 쪽은 같은 폭으로 문제만. 앱은 `_img_split`(막대)·📐(글 상자 위치).
  예전 그림 위 칸 방식(`Form.pages`, `find_form_regions`)은 올릴 때 고를 수 있게 남겨 두었다.
  .hwp는 `forms.hwp_preview`(olefile)로 첫 쪽 미리보기 그림만 꺼내 그림 양식으로 쓴다.
- `worksheet_maker/saved.py` — 저장한 HTML 안의 `<script type="application/json" id="hanjang-data">`에 구조·테마·학교급·꼬리말을 담고(`pack`), 다시 꺼낸다(`unpack`).
  저장 형식을 바꿀 때는 `FORMAT_VERSION`을 올리고 예전 파일도 열리게 유지한다. (2: `form` 추가, 3: `form.doc` 한글 양식)
- **배포**(키는 각자): `app.LOCAL`(`HANJANG_LOCAL=1`, `run.bat`·포터블·`.claude/launch.json`의 app만 준다)일 때만 서버 `.env` 키를
  읽고, '이 컴퓨터에 키 기억하기'(`_remember_key` → `.env`), 양식·모양 디스크 저장을 쓴다. 그 밖(Streamlit Cloud 인터넷판)은 키를
  세션에만 두고(사용자가 '이 브라우저에 키 기억하기'를 켜면 브라우저 쿠키 `hanjang_key`에만 — `st.html` 스크립트로 쓰고
  다음 접속 때 `st.context.cookies`로 읽는다. 값은 키 모양 `[A-Za-z0-9_-]{20,80}`일 때만 쓰고 읽는다. 서버 디스크에는 두지 않는다), 양식은 `.hanjang-form.json` 내려받기·파일 열기, 모양 저장은 끈다(여러 사람이 한 서버를 쓰므로). 포터블은
  `tools/build_portable.py`(임베디드 파이썬 + `pip --target`, `psutil` 추가) → `dist/`. 포터블 실행은
  `tools/portable_launcher.py`(→ `app/launcher.py`, pythonw로 창 없이): 서버를 8517번에 켜거나 이미 켜진 것을 쓰고, 엣지(없으면
  크롬) `--app` 창을 한장 전용 프로필(`%LOCALAPPDATA%/hanjang/window`)로 열어, 그 창이 모두 닫히면 서버를 끈다. 실행 .bat은 CP949로 쓴다. `.gitignore`는 `.env`·`forms/*.json`·직접 저장한 모양을 뺀다.
- `app.py` — Streamlit 화면. 초보자용 **단계형 흐름**(`ss.page`):
  `home`(카드 3개) → 한 과목 `s1`→`s2`→`s3` / 융합 `f1`→`f2`→`f3` / 내 자료 `tools` → 완성 `result`.
  - 화면을 옮겨도 남아야 하는 값은 위젯 키가 아닌 별도 키에 둔다: `ss.grade`, `ss.subject`, `ss.fusion_subjects`,
    `ss.doc_level`(학교급), `ss.footer_text`, `ss.answers_on`, 고른 성취기준 `ss.sel`. 위젯은 `w_*` 키 + `on_change=_sync`.
    (Streamlit은 그 화면에 그려지지 않은 위젯의 값을 지운다.) 학년마다 과목 목록이 달라 과목 위젯 키에 학년을 붙인다.
  - 만들기는 `_run(req)` 하나로: `req`를 `ss.last_req`에 저장해 완성 화면의 '🔄 다시 만들기'에 쓴다.
  - 완성 화면: 왼쪽 탭(내용 고치기 `block_list` / 모양 `theme_gallery` / 제목·목표 `header_panel`+AI 채우기 / 더 많은 설정,
    `st.tabs(key="res_tab")`라 코드로 탭을 고를 수 있다), 오른쪽 미리보기 `preview_editor`.
  - **학습지 위에서 바로 고치기**: `render(edit=True)`가 블록마다 `data-bi`(blocks.html.j2의 `{{ ed }}`)를 달고
    `templates/edit.html.j2`(도구 막대·끌어서 옮기기·Ctrl+Z, 인쇄 때 숨김)를 넣는다 — 저장·인쇄용 HTML에는 넣지 않는다.
    미리보기는 `st.components.v2` 컴포넌트(`_PREVIEW_JS`)가 sandbox iframe으로 그리고, 틀 안의 postMessage를 `setTriggerValue('action')`로
    앱에 넘긴다. `_preview_action`이 값을 다시 검사해 적용(옮기기·빼기·⋯는 그 칸 펼치기·🔄는 `ss.ai_pending` → `_ask_ai` 추천안,
    `text`는 그 자리 고치기 창의 글 — 그 칸 종류의 `FIELDS` 글 칸만(`_inline_fields`), `lines`는 답 칸 손잡이 — 1~20줄).
    칸마다 고칠 원래 글·답 줄 수는 `_edit_data`가 컴포넌트 data로 넘기고, 틀이 'ready'를 보내면 postMessage('fields')로 넣는다
    (틀 안에서는 textarea.value로만 쓴다). 틀을 다시 그릴지는 HTML + fields의 md5로 정한다.
    구조를 바꾸는 편집(`_move`·`_copy`·`_delete`·`_apply_suggestion`·끌기)은 `_snapshot()`으로 `ss.ws_hist`(30개)에 앞 상태를 두고
    `_undo()`가 되살리며 그 칸들의 입력칸 키를 지운다. 펼침 칸 키는 `{bid}_exp{exp_rev}` — 이미 그려진 expander는 expanded 값을
    무시하므로 ✏️ 때 `exp_rev`를 올려 새로 만든다. 실제 앱 시험은 헤드리스 엣지 + DevTools(틀은 OOPIF라 Target.setAutoAttach). 화면이 바뀌면 `st.html(..., unsafe_allow_javascript=True)`로 맨 위로 스크롤한다.
  - 상태: `ss.ws`(dict, 블록마다 편집용 `_id`), `ss.theme`(dict). 통째로 바꿀 때는 `set_worksheet()`/`set_theme()`을 써서
    `rev`를 올려야 입력칸이 새 값으로 다시 그려진다.
- 테마 선택지에 frame(쪽 테두리: 인쇄 시 `.page-frame`이 position:fixed로 쪽마다 반복), qstyle(문항 꾸밈), deco(소제목 아이콘),
  size(글자 크기)가 있다. 기본 제공 모양 순서는 `theme.PRESET_ORDER`.
- 그림 카드(`scene`)·그림그래프(`pictograph`)·묶음 그림의 이모지 `item`은 Noto Emoji(흑백) 글꼴로 SVG에 그린다.
  SVG 속성 안 글꼴 이름은 작은따옴표로 써야 한다(큰따옴표면 style 속성이 깨진다).

## 버전·배포 (롤백할 수 있게)
- 새 작업은 `dev` 갈래에 커밋한다. `main`은 인터넷판(Streamlit Cloud가 main을 자동 배포)이므로 사용자가 배포하라고 할 때만
  dev → main 합치기 + `vX.Y` 태그 + push. 되돌리기는 main을 이전 태그로 (사용자 확인 후).
- 바뀐 점은 `CHANGELOG.md`(개발노트, 선생님이 읽는 말로)의 '작업 중'에 적고, 배포할 때 그 칸에 버전 번호·날짜를 붙인다.

## 새 구성요소 추가 순서 (예: 선 잇기)
1. `schema.BLOCK_TYPES`에 type, 이름, AI용 설명, 사용 필드를 추가 (문항이면 `QUESTION_TYPES`에도)
2. `app.FIELDS`에 편집 칸(필드, 라벨)을 추가
3. `worksheet.html.j2`에 `{% elif b.type == "..." %}` 분기, `worksheet.css`에 스타일 추가 (테마 변수 사용)
4. `examples/sample.json`에 예시 블록을 넣고 세 가지 테마로 렌더링해 A4 인쇄 미리보기로 확인

## 새 스타일 선택지 추가 순서
1. `theme.THEME_OPTIONS`의 해당 항목에 값과 설명 추가 (새 항목이면 `Theme` 모델 필드도)
2. `worksheet.css`에 해당 클래스(예: `.hd-새값 ...`) 규칙 추가
