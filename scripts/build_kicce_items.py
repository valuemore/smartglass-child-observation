"""KICCE 유아관찰척도 리소스 생성기.

권위 소스: resources/child_observation_scale.xlsx (KICCE 유아관찰척도 원본)
산출물:   resources/kicce_items.json (매핑 서비스가 읽는 문항 데이터)

원칙(중요):
- 원본의 '관찰수준1~4'(level)는 **참조 텍스트로만** 보존한다(매칭 보조용).
  본 시스템은 유아에게 관찰수준/발달/평정 **점수를 산출하지 않는다.**
- item_id 는 영역 순서대로 부여한 전역 일련번호다(원본 번호는 영역별로 1부터 재시작).

실행: python scripts/build_kicce_items.py
의존: openpyxl (빌드 시에만 필요. 런타임 앱은 json 만 읽는다)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl

_ROOT = Path(__file__).resolve().parent.parent
_XLSX = _ROOT / "resources" / "child_observation_scale.xlsx"
_OUT = _ROOT / "resources" / "kicce_items.json"

# 컬럼 인덱스 (0-based): 영역, 번호, 문항내용, 관찰수준1~4, 상황, 관찰사례1~4
_COL_AREA, _COL_NUM, _COL_TEXT = 0, 1, 2
_COL_LEVELS = (3, 4, 5, 6)
_COL_EXAMPLES = (8, 9, 10, 11)

# 한국어 조사·어미 간이 제거(키워드 추출 보조)
_PARTICLES = ("으로", "에서", "에게", "하고", "하며", "하는", "한다", "했다",
              "처럼", "보다", "까지", "부터", "마다", "라고")
_JOSA = set("을를이가은는에의로와과도만께요")


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"[\s,.\"'()\[\]·…!?~]+", text or ""):
        w = raw.strip()
        if len(w) < 2:
            continue
        for p in _PARTICLES:
            if w.endswith(p) and len(w) - len(p) >= 2:
                w = w[: -len(p)]
                break
        if len(w) > 2 and w[-1] in _JOSA:
            w = w[:-1]
        if len(w) >= 2:
            out.append(w)
    return out


def _dedup(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    res: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def build() -> list[dict]:
    wb = openpyxl.load_workbook(_XLSX, read_only=True, data_only=True)
    ws = wb.active

    items: list[dict] = []
    cur: dict | None = None
    cur_area: str | None = None

    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:  # 헤더
            continue
        area = row[_COL_AREA]
        num = row[_COL_NUM]
        text = row[_COL_TEXT]
        if area:
            cur_area = area

        if num is not None and text:
            # 새 문항 시작
            cur = {
                "item_id": len(items) + 1,
                "area": cur_area,
                "item_text": str(text).strip(),
                "keywords": [],
                "level_descriptions": [
                    str(row[c]).strip() for c in _COL_LEVELS if row[c]
                ],
                "example_cases": [],
            }
            items.append(cur)

        # 모든 하위 행(상황별)의 관찰사례를 현재 문항에 누적
        if cur is not None:
            for c in _COL_EXAMPLES:
                if row[c]:
                    cur["example_cases"].append(str(row[c]).strip())

    # 키워드 추출: 문항내용 + 관찰사례 텍스트 기반
    for it in items:
        kws = _tokens(it["item_text"]) + _tokens(" ".join(it["example_cases"]))
        it["keywords"] = _dedup(kws)[:25]

    return items


def main() -> None:
    items = build()
    _OUT.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    from collections import Counter
    by_area = Counter(it["area"] for it in items)
    print(f"생성 완료: {_OUT} (총 {len(items)}문항)")
    for area, n in by_area.items():
        print(f"  - {area}: {n}")
    print("참고: 관찰수준(level)은 참조 텍스트로만 보존하며 유아 점수로 산출하지 않습니다.")


if __name__ == "__main__":
    main()
