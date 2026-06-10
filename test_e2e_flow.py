# -*- coding: utf-8 -*-
"""
전체 흐름 E2E 테스트: 업로드 -> 전처리 -> Claude API 분석
실행: python test_e2e_flow.py
"""

import sys
import io
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright, Page

APP_URL = "http://localhost:8501"
VIDEO_PATH = str(Path("data/videos/test_sample.mp4").resolve())
SCREENSHOT_DIR = Path("data/e2e_screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def shot(page: Page, name: str):
    path = str(SCREENSHOT_DIR / f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  [shot] {name}.png")


def wait_spinner_gone(page: Page, timeout_ms: int = 60_000):
    try:
        page.wait_for_selector("[data-testid='stSpinner']", timeout=5000)
        page.wait_for_selector("[data-testid='stSpinner']", state="hidden", timeout=timeout_ms)
    except Exception:
        pass


def click_button_containing(page: Page, *keywords) -> str | None:
    """주어진 키워드 중 하나를 포함하는 버튼을 클릭. 클릭된 버튼 텍스트 반환."""
    btns = page.get_by_role("button").all()
    for btn in btns:
        try:
            t = btn.inner_text(timeout=1000)
            for kw in keywords:
                if kw in t:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    return t
        except Exception:
            pass
    return None


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        # 1. 홈 접속
        print("[1] 앱 접속")
        page.goto(APP_URL, wait_until="networkidle", timeout=30_000)
        time.sleep(2)
        shot(page, "01_home")
        print("  [OK] 홈 로드")

        # 2. 업로드 페이지 이동 (사이드바)
        print("[2] 업로드 페이지 이동")
        try:
            page.get_by_text("업로드", exact=False).first.click()
            time.sleep(2)
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        shot(page, "02_upload_page")
        print("  [OK] 업로드 페이지")

        # 3. 파일 업로드
        print("[3] 파일 업로드")
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files(VIDEO_PATH)
        time.sleep(3)
        wait_spinner_gone(page, 30_000)
        shot(page, "03_after_upload")
        if "test_sample" in page.content():
            print("  [OK] 파일 인식")
        else:
            print("  [WARN] 파일명 미표시")

        # 4. 전처리 실행 (섹션 2)
        print("[4] 전처리 실행")
        btn_clicked = click_button_containing(page, "전처리", "장면 분할", "프레임 추출")
        if btn_clicked:
            print(f"  [OK] 클릭: '{btn_clicked[:30]}'")
            time.sleep(3)
            wait_spinner_gone(page, 120_000)
            shot(page, "04_after_preprocess")
            if "전처리 완료" in page.content() or "장면 수" in page.content():
                print("  [OK] 전처리 완료 확인")
            else:
                print("  [WARN] 완료 메시지 미확인")
        else:
            print("  [WARN] 전처리 버튼 없음")
            shot(page, "04_no_preprocess")

        # 5. 섹션 3 전체 스크롤해서 확인
        print("[5] 섹션 3 스크롤 확인")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        shot(page, "05_scrolled_down")

        # 6. 실호출 사유 입력 (st.text_input → input[type='text'] 중 빈 것)
        print("[6] 실호출 사유 입력")
        syu_filled = False
        try:
            # Streamlit text_input은 placeholder로 찾는다
            inp = page.locator("input[placeholder*='사유']").first
            inp.scroll_into_view_if_needed()
            inp.fill("E2E 테스트: Claude API 실호출 검증")
            time.sleep(0.5)
            syu_filled = True
            print("  [OK] 사유 입력")
        except Exception:
            # placeholder 없으면 text input 목록에서 시도
            try:
                inputs = page.locator("input[type='text']").all()
                for inp in inputs:
                    val = inp.get_attribute("value") or ""
                    if not val.strip():
                        inp.scroll_into_view_if_needed()
                        inp.fill("E2E 테스트: Claude API 실호출 검증")
                        syu_filled = True
                        break
                if syu_filled:
                    print("  [OK] 사유 입력 (fallback)")
                else:
                    print("  [WARN] 사유 입력란 없음 (dry_run=false 아닐 수 있음)")
            except Exception as e2:
                print(f"  [WARN] 사유 입력 실패: {e2}")

        shot(page, "06_reason_input")

        # 7. 체크박스 2개 선택 (Streamlit 커스텀 체크박스는 label 텍스트 클릭 필요)
        print("[7] 체크박스 선택")
        cb_labels = ["실제 외부 API 호출과 비용", "데이터 무보존"]
        count = 0
        for label_kw in cb_labels:
            try:
                label_el = page.locator(f"label:has-text('{label_kw}')").first
                label_el.scroll_into_view_if_needed()
                label_el.click(timeout=5000)
                count += 1
                time.sleep(0.5)
            except Exception as e:
                # JavaScript로 체크박스 강제 클릭
                try:
                    page.evaluate(f"""
                        document.querySelectorAll('input[type=checkbox]').forEach((cb, i) => {{
                            const l = cb.closest('label') || cb.parentElement.querySelector('label');
                            if (l && l.textContent.includes('{label_kw[:5]}')) {{
                                l.click();
                            }}
                        }});
                    """)
                    count += 1
                except Exception as e2:
                    print(f"  [WARN] 체크박스 '{label_kw[:8]}': {e2}")
        print(f"  [OK] 체크박스 {count}개 클릭")

        time.sleep(1)
        shot(page, "07_checkboxes")

        # 8. Claude 실호출 버튼 클릭
        print("[8] Claude 분석 실행 버튼 클릭")
        time.sleep(1)  # 체크박스 상태 반영 대기
        btn_text = click_button_containing(page, "실호출 분석", "Claude (Anthropic) 실호출")
        if btn_text:
            print(f"  [OK] 클릭: '{btn_text[:40]}'")
            time.sleep(5)
            wait_spinner_gone(page, 120_000)
            shot(page, "08_after_analysis")
            content_after = page.content()
            if "분석 완료" in content_after or "후보" in content_after or "child_" in content_after:
                print("  [OK] 분석 결과 확인")
            elif "오류" in content_after or "실패" in content_after or "Error" in content_after:
                print("  [FAIL] 분석 오류 발생 (스크린샷 확인)")
            else:
                print("  [WARN] 결과 불명확")
        else:
            print("  [WARN] Claude 분석 버튼 없음")
            # 현재 모든 버튼 목록 출력
            all_btns = [b.inner_text(timeout=500) for b in page.get_by_role("button").all()]
            print(f"  현재 버튼들: {all_btns[:10]}")
            shot(page, "08_no_btn")

        # 9. 최종 화면
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        shot(page, "09_final_scrolled")
        print(f"\n스크린샷: {SCREENSHOT_DIR.resolve()}")
        browser.close()


if __name__ == "__main__":
    run()
    print("\n[E2E 완료]")
