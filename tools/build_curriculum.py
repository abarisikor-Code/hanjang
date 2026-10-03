"""2022 개정 교육과정 초등 성취기준을 국가교육과정정보센터(NCIC) 원문에서 그대로 가져와 JSON으로 만든다.

실행: python tools/build_curriculum.py
결과: data/curriculum_elementary.json

성취기준 문장은 사람이 옮겨 적지 않고 이 스크립트가 원문에서 뽑는다(오타·누락 방지).
원문의 고유 코드 개수와 뽑은 개수가 다르면 저장하지 않고 멈춘다.
"""

from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://ncic.re.kr"
OUT = Path(__file__).resolve().parent.parent / "data" / "curriculum_elementary.json"

# NCIC 원문 인벤토리: 2022 개정 시기 > 초등학교(2022.12) > 과목 > 2. 내용 체계 및 성취기준 > 나. 성취기준
PAGES = [
    ("국어", "1-2", 10069355), ("국어", "3-4", 10069356), ("국어", "5-6", 10069357),
    ("수학", "1-2", 10069376), ("수학", "3-4", 10069377), ("수학", "5-6", 10069378),
    ("영어", "3-4", 10069427), ("영어", "5-6", 10069428),
    ("사회", "3-4", 10069392), ("사회", "5-6", 10069393),
    ("과학", "3-4", 10069390), ("과학", "5-6", 10069391),
]

BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "td", "th"}


class _Text(HTMLParser):
    """브라우저 innerText처럼: 블록 태그마다 줄을 바꾸고, script·style은 버린다."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = (re.sub(r"[ \t\r 　]+", " ", line).strip() for line in raw.split("\n"))
        return "\n".join(line for line in lines if line)


def _opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "hanjang-curriculum-builder/1.0"), ("Referer", BASE + "/inv/org/list.do")]
    html = op.open(BASE + "/inv/org/list.do", timeout=30).read().decode("utf-8", "replace")
    m = re.search(r'<meta name="_csrf" content="([^"]+)"', html)
    if not m:
        sys.exit("NCIC 페이지에서 보안 토큰(_csrf)을 찾지 못했습니다. 사이트 구조가 바뀌었을 수 있어요.")
    return op, m.group(1)


def fetch_text(op, csrf: str, seq: int) -> str:
    body = urllib.parse.urlencode(
        {"_csrf": csrf, "openYear": "2015", "location": "", "seq": str(seq), "orgType": "org", "nationCd": ""}
    ).encode()
    html = op.open(BASE + "/inv/org/view.do", data=body, timeout=60).read().decode("utf-8", "replace")
    p = _Text()
    p.feed(html)
    text = p.text()
    # 코드 안의 여러 종류 줄표(–, — 등)를 '-'로 통일: [4사01–01] → [4사01-01]
    text = re.sub(r"\[(\d[가-힣]{1,2}\d{2})\s*[‐‑‒–—―-]\s*(\d{2})\]", r"[\1-\2]", text)
    i = text.find("찜하기")
    return text[i + 3:] if i >= 0 else text


CODE = re.compile(r"^\[(\d[가-힣]{1,2}\d{2}-\d{2})\]\s*(.*)$")
AREA = re.compile(r"^\((\d{1,2})\)\s*(\S.*)$")
TOPIC = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ][.,·\s]*(\S.*)$")  # 예: "Ⅳ, 시각과 시간"


def parse(text: str) -> list[dict]:
    areas: list[dict] = []
    area = last = None
    mode, topic = "list", ""
    for line in text.split("\n"):
        line = line.strip()
        m = AREA.match(line)
        if m and "성취기준" not in line:
            area = {"no": int(m.group(1)), "name": m.group(2).strip(), "standards": []}
            areas.append(area)
            mode, topic, last = "list", "", None
            continue
        if re.match(r"^\(가\)\s*성취기준\s*해설", line):
            mode, last = "explain", None
            continue
        if re.match(r"^\(나\)\s*성취기준\s*적용", line):
            mode, last = "apply", None
            continue
        if area is None:
            continue
        if mode == "list":
            if m := TOPIC.match(line):
                topic, last = m.group(1).strip(), None
            elif m := CODE.match(line):
                last = {"code": m.group(1), "text": m.group(2).strip(), "topic": topic, "explain": ""}
                area["standards"].append(last)
            elif last and not last["text"].endswith("다."):
                last["text"] += " " + line  # 한 문장이 두 줄로 나뉜 경우
            elif len(line) <= 25 and not line.startswith(("(", "[", "※", "*")):
                topic, last = line, None  # 번호 없이 적힌 소주제 (예: "각도")
        elif mode == "explain":
            if m := CODE.match(line):
                last = next((s for s in area["standards"] if s["code"] == m.group(1)), None)
                if last:
                    last["explain"] = m.group(2).strip()
            elif last:
                last["explain"] += " " + line
    return areas


def main() -> None:
    op, csrf = _opener()
    subjects: dict[str, dict] = {}
    problems = []
    for subject, band, seq in PAGES:
        text = fetch_text(op, csrf, seq)
        areas = parse(text)
        got = [s["code"] for a in areas for s in a["standards"]]
        prefix = band.split("-")[1] + subject[0]
        listed = sorted(set(re.findall(rf"\[({prefix}\d{{2}}-\d{{2}})\]", text)))
        unfinished = [s["code"] for a in areas for s in a["standards"] if not s["text"].endswith("다.")]
        print(f"{subject} {band}: 영역 {len(areas)}, 성취기준 {len(got)} / 원문 코드 {len(listed)}")
        if sorted(got) != listed or len(got) != len(set(got)) or unfinished:
            problems.append(f"{subject} {band}: 빠짐 {set(listed) - set(got)}, 문장 미완성 {unfinished}")
        subjects.setdefault(subject, {})[band] = {"source_seq": seq, "areas": areas}

    if problems:
        sys.exit("원문과 맞지 않아 저장하지 않았습니다:\n" + "\n".join(problems))

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "국가교육과정정보센터(NCIC) 원문 인벤토리 — 2022 개정 교육과정 초등학교(2022.12), 나. 성취기준",
        "source_url": BASE + "/inv/org/view.do (seq: source_seq)",
        "built": dt.date.today().isoformat(),
        "subjects": subjects,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(s["standards"]) for b in subjects.values() for x in b.values() for s in x["areas"])
    print(f"저장: {OUT} (성취기준 {total}개)")


if __name__ == "__main__":
    main()
