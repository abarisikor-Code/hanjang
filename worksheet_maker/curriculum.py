"""2022 개정 교육과정 초등 성취기준 + 학년 배치·학부모용 설명.

- data/curriculum_elementary.json: 성취기준 원문 (tools/build_curriculum.py가 NCIC 원문에서 생성)
- data/guide/<과목>.json: 학년 배치(grades, when)와 학부모용 쉬운 설명(easy) (tools/check_guide.py로 검사)

성취기준 자체는 학년군(1~2, 3~4, 5~6학년) 단위다. 학년 배치는 교과서 단원 구성을 따른 것이며
수학은 출판사에 따라 학기가 조금 다를 수 있다. 국어·영어는 같은 성취기준을 두 학년에 걸쳐 배운다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "curriculum_elementary.json"
GUIDE_DIR = ROOT / "data" / "guide"

# 2022 개정 교육과정에서 초등 과목이 시작되는 학년 (영어·사회·과학은 3학년부터)
SUBJECT_START = {"국어": 1, "수학": 1, "영어": 3, "사회": 3, "과학": 3}


@dataclass(frozen=True)
class Standard:
    code: str  # 예: 4수01-03
    text: str  # 성취기준 원문
    area: str  # 영역 (예: 수와 연산)
    topic: str  # 영역 안의 소주제 (수학)
    explain: str  # 성취기준 해설 (있을 때만)
    easy: str = ""  # 학부모용 쉬운 설명
    grades: tuple[int, ...] = ()  # 주로 배우는 학년
    when: str = ""  # 예: "3학년 1학기 (덧셈과 뺄셈)"
    scope: tuple[tuple[int, str], ...] = ()  # 두 학년에 걸친 성취기준의 학년별 범위 ((3, "…"), (4, "…"))

    def scope_for(self, grade: int) -> str:
        return dict(self.scope).get(grade, "")


def band_of(grade: int) -> str:
    return {1: "1-2", 2: "1-2", 3: "3-4", 4: "3-4", 5: "5-6", 6: "5-6"}[grade]


def band_label(grade: int) -> str:
    return band_of(grade).replace("-", "~") + "학년"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _guide(subject: str) -> dict:
    path = GUIDE_DIR / f"{subject}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def source() -> str:
    d = _data()
    return f"{d['source']} (가져온 날: {d['built']})"


def _make(subject: str, band: str, area: dict, s: dict) -> Standard:
    g = _guide(subject).get(s["code"], {})
    lo, hi = (int(x) for x in band.split("-"))
    return Standard(
        code=s["code"], text=s["text"], area=area["name"], topic=s.get("topic", ""), explain=s.get("explain", ""),
        easy=g.get("easy", ""), grades=tuple(g.get("grades", (lo, hi))), when=g.get("when", ""),
        scope=tuple(sorted((int(k), v) for k, v in g.get("scope", {}).items())),
    )


def subjects_for(grade: int) -> list[str]:
    return [s for s, start in SUBJECT_START.items() if grade >= start and band_of(grade) in _data()["subjects"].get(s, {})]


def standards(grade: int, subject: str, area: str | None = None, this_grade_only: bool = True) -> list[Standard]:
    """그 학년에서 배우는 성취기준. this_grade_only=False면 학년군 전체."""
    band = band_of(grade)
    out = []
    for a in _data()["subjects"][subject][band]["areas"]:
        if area and a["name"] != area:
            continue
        for s in a["standards"]:
            std = _make(subject, band, a, s)
            if not this_grade_only or grade in std.grades:
                out.append(std)
    return out


def areas(grade: int, subject: str, this_grade_only: bool = True) -> list[str]:
    seen: list[str] = []
    for s in standards(grade, subject, this_grade_only=this_grade_only):
        if s.area not in seen:
            seen.append(s.area)
    return seen


def find(codes: list[str]) -> list[Standard]:
    wanted = set(codes)
    found = []
    for subject, bands in _data()["subjects"].items():
        for band, data in bands.items():
            for a in data["areas"]:
                found += [_make(subject, band, a, s) for s in a["standards"] if s["code"] in wanted]
    return sorted(found, key=lambda s: codes.index(s.code))


def catalog() -> str:
    """말로 고르기용 전체 목록 한 줄씩: 코드 | 과목 | 배우는 학년 | 영역 | 쉬운 설명(없으면 원문)."""
    lines = []
    for subject, bands in _data()["subjects"].items():
        for band in bands:
            grade = int(band.split("-")[0])
            for s in standards(grade, subject, this_grade_only=False):
                grades = ",".join(str(g) for g in s.grades) or band
                lines.append(f"{s.code} | {subject} | {grades} | {s.area} | {s.easy or s.text}")
    return "\n".join(lines)


def _all(subject: str) -> list[Standard]:
    out = []
    for band in _data()["subjects"].get(subject, {}):
        out += standards(int(band.split("-")[0]), subject, this_grade_only=False)
    return out


# 과목·학년별 기본 수준 (성취기준이 학년군 단위라 생기는 빈틈을 메운다: 같은 성취기준이라도 3학년과 4학년은 다르다)
# 2022 개정 교육과정 해설과 교과서 학년 배치를 바탕으로 사람이 정리한 것.
GRADE_NOTES: dict[str, dict[int | str, str]] = {  # 학년(int)별 수준, "억양"처럼 학년과 상관없는 사실(str)
    "수학": {
        1: "수는 0~100만 쓴다. 덧셈·뺄셈만 하고 곱셈·나눗셈·분수·소수·길이 단위(cm)는 쓰지 않는다. 시각은 '몇 시', '몇 시 30분'만. "
           "도형은 '상자·둥근기둥·공 모양', '세모·네모·동그라미 모양'으로만 부른다(삼각형·원·직육면체 같은 용어는 쓰지 않는다).",
        2: "수는 네 자리 수(9999)까지. 곱셈은 곱셈구구(한 자리 × 한 자리)까지만, 나눗셈·분수·소수는 쓰지 않는다. 길이 단위는 cm·m만. "
           "도형 용어는 삼각형·사각형·원·변·꼭짓점·곧은 선·굽은 선까지(직사각형·정사각형·직각·선분·각은 3학년). "
           "그래프는 ○·×로 나타낸 간단한 그래프만.",
        3: "수는 네 자리 수까지(만 이상은 4학년). 분수는 분수의 뜻·종류·크기 비교까지(분수의 덧셈·뺄셈은 4학년), 소수는 소수 한 자리(0.1 단위)까지. "
           "그래프는 그림그래프까지(막대그래프는 4학년). 각도(°)·예각·둔각은 4학년.",
        4: "분수의 덧셈·뺄셈은 분모가 같은 것만(약분·통분은 5학년). 소수는 세 자리까지 읽고 두 자리까지 더하고 뺀다(소수의 곱셈·나눗셈은 5~6학년).",
        5: "분수의 나눗셈, 소수의 나눗셈, 비와 비율·백분율, 원주율, 부피는 6학년에서 배운다.",
        6: "중학교 내용(문자 x를 쓰는 방정식, 거듭제곱, 음수)은 쓰지 않는다.",
    },
    "국어": {
        1: "1학년은 한글을 익히는 중이다. 지시문·문장은 아주 짧게(한 문장 10어절 이내), 읽기 글은 3~5문장, 겹받침이 있는 낱말과 어려운 한자어는 피한다.",
        2: "읽기 글은 짧은 이야기나 설명(10문장 안팎). 2학년이 아는 쉬운 낱말로 쓴다.",
        3: "읽기 글은 2~3문단. 문단·중심 문장 같은 용어는 쉽게 풀어서 쓴다.",
        4: "읽기 글은 3~4문단. 사실과 의견, 높임 표현 등 그 학년 용어를 쓸 수 있다.",
        5: "읽기 글은 3~5문단. 주장·근거, 비유 표현 등 5~6학년군 용어를 쓸 수 있다.",
        6: "읽기 글은 4~6문단. 논설문·토론 등 6학년 수준까지.",
    },
    "영어": {
        3: "영어를 처음 배우는 학년이다. 그림으로 보여 줄 수 있는 쉬운 낱말과 아주 짧은 표현(문장은 7낱말 이내)만 쓴다. "
           "영어 문장 쓰기는 하지 않고 알파벳·낱말 수준으로. 지시문은 모두 한국어로 쓴다.",
        4: "쉬운 낱말과 짧은 문장(7낱말 이내). 쓰기는 낱말·짧은 어구까지. 지시문은 한국어로 쓴다.",
        "억양": "억양을 다룰 때: What·Where·How 같은 의문사로 시작하는 질문은 끝을 내려 읽고, "
                "Do you…?처럼 Yes/No로 답하는 질문은 끝을 올려 읽는다.",
        5: "일상 주제의 간단한 문장(9낱말 이내)과 3~5문장의 짧은 글. 지시문은 한국어로 쓴다.",
        6: "간단한 문장(9낱말 이내)과 짧은 글. 문법 용어(관계대명사, 현재완료 등)는 쓰지 않는다. 지시문은 한국어로 쓴다.",
    },
    "사회": {
        3: "3학년은 우리 동네·고장 중심이다. 나라 전체의 정치·역사 사건이나 어려운 한자어 용어는 쓰지 않는다.",
        4: "4학년은 우리 지역(시·도) 중심이다. 우리나라 전체 지리·역사는 5학년부터.",
        5: "5학년은 우리나라 국토·인권·역사(선사~광복·6·25)까지. 세계 지리와 정치 제도는 6학년에서 배운다.",
        6: "6학년 범위 밖의 중학교 개념(수요·공급 곡선 등)은 쓰지 않는다.",
    },
    "과학": {
        3: "관찰·실험 중심의 쉬운 말로 쓴다. 원자·분자·화학식, 전기 회로 같은 고학년 개념은 쓰지 않는다.",
        4: "관찰·실험 중심. 산과 염기, 전기 회로, 지구의 자전·공전 같은 5~6학년 개념은 쓰지 않는다.",
        5: "원자·분자·화학식, 세포 내부 구조 같은 중학교 개념은 쓰지 않는다.",
        6: "원자·분자·화학식 같은 중학교 개념은 쓰지 않는다.",
    },
}


def limits(grade: int, subject: str, chosen: list[Standard] | None = None) -> str:
    """AI에게 주는 학년 범위: 고른 성취기준을 이 학년에서 어디까지 배우는지, 그리고 아직 배우지 않은 내용(다음 두 학년).
    교과서 학년 배치(data/guide)에서 만든다. 실수(예: 4학년 학습지에 약분)를 막기 위한 것."""
    lines = [f"- {grade}학년 {subject} 학습지다. 아래 '아직 배우지 않은 내용'은 문제·보기·풀이·정답 어디에도 쓰지 않는다."]
    if GRADE_NOTES.get(subject, {}).get(grade):
        lines.append(f"- {grade}학년 기본 수준: {GRADE_NOTES[subject][grade]}")
    if GRADE_NOTES.get(subject, {}).get("억양"):  # AI가 자주 틀리는 사실 (영어 의문문 억양)
        lines.append(f"- {GRADE_NOTES[subject]['억양']}")
    if subject == "수학":
        lines.append("- 음수(0보다 작은 수)는 초등학교에서 배우지 않는다.")
    for s in chosen or []:  # 고른 성취기준 자체는 범위 안이다 (다음 학년 내용과 낱말이 겹쳐 보여도)
        here = s.scope_for(grade)
        inside = f"{grade}학년에서는 '{here}'까지만 다룬다. 그 안의 내용은" if here else f"{grade}학년 범위 안이다. 이 성취기준이 다루는 내용은"
        lines.append(f"- 이 학습지의 성취기준 [{s.code}] {s.text} — {inside} "
                     "아래 '아직 배우지 않은 내용'과 낱말이 겹쳐 보여도 쓸 수 있다.")
        if not here and s.when:
            lines.append(f"- [{s.code}] 배우는 때: {s.when}")
    later: dict[int, list[str]] = {}
    for s in _all(subject):
        for g, text in s.scope:  # 두 학년에 걸친 성취기준의 뒤 학년 부분
            if g > grade:
                later.setdefault(g, []).append(text)
        if s.grades and min(s.grades) > grade:
            later.setdefault(min(s.grades), []).append(s.easy or s.text)
    for g in sorted(later)[:2]:
        lines.append(f"- 아직 배우지 않은 내용 ({g}학년): " + " / ".join(dict.fromkeys(later[g])))
    return "\n".join(lines)
