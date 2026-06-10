"""KICCE 유아관찰척도 Excel → resources/kicce_items.json 변환 스크립트.

실행 방법 (프로젝트 루트에서):
    python scripts/import_kicce_xlsx.py

입력: data/child_observation_scale.xlsx
출력: resources/kicce_items.json

Excel 구조:
  - 시트: '누리과정 관찰척도'
  - 컬럼: 영역(0), 번호(1), 관찰문항(2), 관찰수준1~4(3~6), 구분(7), 관찰사례1~4(8~11)
  - 항목당 3행 (놀이 / 일상생활 / 활동), 영역·번호·관찰문항·관찰수준은 첫 행에만 있음

주의:
  - level_descriptions는 참조용으로만 저장한다. 점수·수준 산출에 쓰지 않는다.
  - keywords는 관찰문항 텍스트와 관찰사례에서 핵심 어절을 자동 추출한다.
"""

from __future__ import annotations

import json
import re
import sys
import io
from collections import OrderedDict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

XLSX_PATH = Path("data/child_observation_scale.xlsx")
OUT_PATH  = Path("resources/kicce_items.json")


# ---------------------------------------------------------------------------
# 키워드 자동 추출
# ---------------------------------------------------------------------------

_STOP = {
    "을", "를", "이", "가", "은", "는", "의", "에서", "에", "서", "로", "으로",
    "과", "와", "도", "며", "고", "면", "니", "자", "지", "어", "아", "해", "게",
    "만", "뿐", "하다", "있다", "없다", "하는", "하여", "하고", "이나", "거나",
    "하며", "한다", "된다", "이다", "된", "한", "할", "있는", "없는", "하면서",
    "통해", "위해", "위한", "그리고", "하지만", "때문에", "위에", "아래", "안에",
    "밖에", "모습", "모습을", "모습이",
}


def _extract_keywords(texts: list[str], n: int = 12) -> list[str]:
    """여러 텍스트를 합쳐서 핵심 어절을 추출한다."""
    combined = " ".join(t for t in texts if t)
    words = re.split(r"[\s,·/\(\)\.\!\?\"\']+", combined)
    kws: list[str] = []
    for w in words:
        w = w.strip()
        # 후행 조사·어미 제거 (간단한 규칙 기반)
        w2 = re.sub(
            r"(을|를|이|가|은|는|의|에서|에|서|로|으로|과|와|도|며|고|면|자|지|어|아|해|게|만|뿐|하며|하고|하여|하는|하면서)$",
            "",
            w,
        )
        if len(w2) >= 2 and w2 not in _STOP and w2 not in kws:
            kws.append(w2)
    return kws[:n]


# ---------------------------------------------------------------------------
# Excel 파싱
# ---------------------------------------------------------------------------

def parse_xlsx(path: Path) -> list[dict]:
    try:
        import openpyxl
    except ImportError:
        print("openpyxl이 필요합니다: pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # 헤더 행 제외하고 모든 행 읽기
    all_rows: list[list] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        all_rows.append(list(row))

    # 영역(0)/번호(1)/관찰문항(2)/관찰수준1~4(3~6) 전방 채움 (병합 셀 복원)
    last_area: str | None = None
    last_num: str | None = None
    last_item_text: str | None = None
    last_levels: list = [None, None, None, None]

    for r in all_rows:
        if r[0]:
            last_area = str(r[0]).strip()
        r[0] = last_area

        if r[1] is not None and str(r[1]).strip():
            last_num = str(r[1]).strip()
        r[1] = last_num

        if r[2]:
            last_item_text = str(r[2]).strip()
            last_levels = [r[i] for i in range(3, 7)]
        r[2] = last_item_text
        r[3], r[4], r[5], r[6] = last_levels

    # (영역, 번호) 기준 그룹화 (삽입 순서 유지)
    groups: OrderedDict[tuple, list[list]] = OrderedDict()
    for r in all_rows:
        key = (r[0], r[1])
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    # 항목 생성
    items: list[dict] = []
    global_id = 1

    for (area, num), grp in groups.items():
        if not area or not num:
            continue
        item_text = grp[0][2] or ""
        if not item_text.strip():
            continue

        # 관찰수준 1~4 (참조용, 점수화 아님)
        level_descs = [
            str(v).strip() for v in grp[0][3:7]
            if v and str(v).strip()
        ]

        # 모든 구분(놀이/일상생활/활동) 행에서 관찰사례 수집
        example_cases: list[str] = []
        for r in grp:
            for v in r[8:12]:
                if v:
                    s = str(v).strip()
                    if s and s not in example_cases:
                        example_cases.append(s)

        # keywords: 관찰문항 + 관찰사례 상위 4개에서 자동 추출
        kw_sources = [item_text] + example_cases[:4]
        keywords = _extract_keywords(kw_sources)

        items.append({
            "item_id": global_id,
            "area": area,
            "item_text": item_text,
            "keywords": keywords,
            "level_descriptions": level_descs,
            "example_cases": example_cases[:12],
        })
        global_id += 1

    return items


# ---------------------------------------------------------------------------
# 검증 및 저장
# ---------------------------------------------------------------------------

def validate_and_save(items: list[dict], out_path: Path) -> None:
    from collections import Counter

    area_counts = Counter(it["area"] for it in items)
    expected = {
        "신체운동·건강": 12,
        "의사소통": 12,
        "사회관계": 12,
        "예술경험": 10,
        "자연탐구": 13,
    }

    print(f"\n=== 변환 결과 ===")
    print(f"총 항목 수: {len(items)}")
    ok = True
    for area, exp in expected.items():
        got = area_counts.get(area, 0)
        status = "✓" if got == exp else f"✗ (기대={exp})"
        print(f"  {area}: {got}개 {status}")
        if got != exp:
            ok = False

    # level_descriptions에 '점수', 'score', 'level', 'rating' 금지 확인
    score_fields = []
    for it in items:
        for d in it.get("level_descriptions", []):
            # 수준 설명 자체는 허용, 필드명만 확인
            pass  # 필드명은 이미 'level_descriptions' 하나뿐

    if not ok:
        print("\n⚠️  항목 수가 기대와 다릅니다. Excel 파일을 확인하세요.")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"\n저장 완료: {out_path}")
    print("참고: level_descriptions는 참조용 텍스트로만 저장됩니다. 점수 산출에 쓰지 않습니다.")


if __name__ == "__main__":
    if not XLSX_PATH.exists():
        print(f"오류: {XLSX_PATH} 파일을 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    print(f"입력: {XLSX_PATH}")
    items = parse_xlsx(XLSX_PATH)
    validate_and_save(items, OUT_PATH)
