"""data/guide/*.json(학년 배치·학부모용 설명)이 성취기준 데이터와 맞는지 검사한다.

실행: python tools/check_guide.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURRICULUM = json.loads((ROOT / "data" / "curriculum_elementary.json").read_text(encoding="utf-8"))

# 교과서 단원 구성에서 확인한 규칙 (과학: 영역 1~8 낮은 학년 / 9~16 높은 학년, 사회: 영역별 학년·학기)
SCIENCE_SPLIT = 8
SOCIAL = {
    "3-4": {1: "3학년 1학기", 2: "3학년 1학기", 3: "3학년 2학기", 4: "3학년 2학기", 5: "4학년 1학기",
            6: "4학년 1학기", 7: "4학년 1학기", 8: "4학년 2학기", 9: "4학년 2학기", 10: "4학년 2학기"},
    "5-6": {1: "5학년 1학기", 2: "5학년 1학기", 3: "5학년 1학기", 4: "5학년 2학기", 5: "5학년 2학기",
            6: "5학년 2학기", 7: "6학년 1학기", 8: "6학년 1학기", 9: "6학년 1학기", 10: "6학년 2학기",
            11: "6학년 2학기", 12: "6학년 2학기"},
}


def main() -> None:
    problems = []
    for subject, bands in CURRICULUM["subjects"].items():
        guide = json.loads((ROOT / "data" / "guide" / f"{subject}.json").read_text(encoding="utf-8"))
        codes = set()
        for band, data in bands.items():
            lo, hi = (int(x) for x in band.split("-"))
            for area in data["areas"]:
                for s in area["standards"]:
                    code = s["code"]
                    codes.add(code)
                    g = guide.get(code)
                    if not g or not g.get("easy", "").strip():
                        problems.append(f"{code}: 쉬운 설명 없음")
                        continue
                    grades = g.get("grades", [lo, hi])
                    if not grades or any(x not in (lo, hi) for x in grades):
                        problems.append(f"{code}: 학년 {grades}이(가) 학년군 {band} 밖")
                    scope = g.get("scope", {})
                    if scope and sorted(int(k) for k in scope) != sorted(grades):
                        problems.append(f"{code}: 학년별 범위(scope) {sorted(scope)}이(가) 학년 {grades}와 다름")
                    if subject == "과학":
                        want = [lo] if area["no"] <= SCIENCE_SPLIT else [hi]
                        if grades != want:
                            problems.append(f"{code}: 과학 영역 {area['no']}은(는) {want}학년이어야 함 (지금 {grades})")
                    if subject == "사회":
                        when = SOCIAL[band][area["no"]]
                        if not g.get("when", "").startswith(when) or grades != [int(when[0])]:
                            problems.append(f"{code}: 사회 영역 {area['no']}은(는) '{when}'이어야 함")
        extra = set(k for k in guide if not k.startswith("_")) - codes
        if extra:
            problems.append(f"{subject}: 성취기준에 없는 코드 {sorted(extra)}")
        print(f"{subject}: 성취기준 {len(codes)}개 확인")
    if problems:
        sys.exit("문제 발견:\n" + "\n".join(problems))
    print("모든 성취기준에 학년 배치와 쉬운 설명이 있습니다.")


if __name__ == "__main__":
    main()
