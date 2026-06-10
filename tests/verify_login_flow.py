"""로그인·역할 분기 검증 스크립트.

Playwright의 WebSocket 세션 특성:
  - page.goto()로 다른 URL로 이동하면 새 WebSocket 연결 → 세션 초기화
  - 동일 세션 유지: 사이드바 링크 클릭 또는 클라이언트 사이드 라우팅 사용
"""

import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "http://localhost:8501"
RESULTS: dict[str, bool] = {}


def _wait_stable(page, delay=1.5):
    """Streamlit 리렌더 완료를 기다린다."""
    time.sleep(delay)


def _login(page, username: str, password: str) -> bool:
    """현재 페이지의 로그인 폼으로 인증을 시도한다."""
    try:
        page.fill('input[aria-label="사용자 이름"]', username)
        page.fill('input[aria-label="비밀번호"]', password)
        page.click('button:has-text("로그인")')
        _wait_stable(page, 2.5)
        return True
    except Exception as e:
        print(f"  로그인 입력 오류: {e}")
        return False


def run_verification():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ── 테스트 1: 홈 페이지 비인증 → 로그인 폼 표시 ─────────────────
        print("\n[테스트 1] 홈 비인증 → 로그인 폼 표시")
        ctx1 = browser.new_context()
        p1 = ctx1.new_page()
        p1.goto(BASE)
        _wait_stable(p1)
        has_form = p1.locator('button:has-text("로그인")').count() > 0
        RESULTS["홈 비인증 → 로그인 폼"] = has_form
        print(f"  로그인 폼 표시: {has_form}")
        p1.screenshot(path="data/e2e_screenshots/v3_01_unauth.png")
        ctx1.close()

        # ── 테스트 2: 교사 로그인 후 홈 페이지 콘텐츠 접근 ───────────────
        print("\n[테스트 2] 교사 로그인 → 홈 콘텐츠 접근")
        ctx2 = browser.new_context()
        p2 = ctx2.new_page()
        p2.goto(BASE)
        _wait_stable(p2)
        ok = _login(p2, "teacher", "1234")
        if ok:
            p2.screenshot(path="data/e2e_screenshots/v3_02_teacher_home.png")
            # 로그인 폼이 사라지고 역할 배지가 표시되는지 확인
            form_gone = p2.locator('button:has-text("로그인")').count() == 0
            has_badge = "teacher" in p2.content().lower()
            RESULTS["교사 로그인 성공 (폼 사라짐)"] = form_gone
            RESULTS["교사 역할 배지 표시"] = has_badge
            print(f"  로그인 폼 사라짐: {form_gone}")
            print(f"  역할 배지 표시: {has_badge}")

            # 사이드바에서 일일 영상 기록 페이지로 이동 (클릭 기반)
            try:
                p2.locator('[data-testid="stSidebarNav"] a').filter(has_text="영상").click()
                _wait_stable(p2, 2.0)
                p2.screenshot(path="data/e2e_screenshots/v3_03_teacher_upload.png")
                upload_loaded = p2.locator("h1, h2, h3").filter(has_text="영상").count() > 0
                RESULTS["교사 업로드 페이지 이동"] = upload_loaded
                print(f"  업로드 페이지 이동: {upload_loaded}")

                # 연구자 전용 UI(장면 분할 상세 옵션)가 숨겨져 있는지 확인
                scene_slider = p2.locator('text=장면 수 상한').count()
                RESULTS["교사: 장면 수 슬라이더 숨김"] = scene_slider == 0
                print(f"  장면 수 슬라이더 숨김: {scene_slider == 0}")
            except PWTimeout:
                RESULTS["교사 업로드 페이지 이동"] = False
                print("  업로드 페이지 이동 실패 (타임아웃)")
        ctx2.close()

        # ── 테스트 3: 연구자 로그인 → 리포트 접근 허용 ──────────────────
        print("\n[테스트 3] 연구자 로그인 → 리포트 접근 허용")
        ctx3 = browser.new_context()
        p3 = ctx3.new_page()
        p3.goto(BASE)
        _wait_stable(p3)
        _login(p3, "researcher", "1234")
        try:
            p3.locator('[data-testid="stSidebarNav"] a').filter(has_text="연구자").click()
            _wait_stable(p3, 2.0)
            p3.screenshot(path="data/e2e_screenshots/v3_04_researcher_report.png")
            blocked = p3.locator('text=연구자 전용').count() > 0
            report_ok = p3.locator("h1, h2, h3").filter(has_text="리포트").count() > 0
            RESULTS["연구자 리포트 접근 허용"] = report_ok
            print(f"  리포트 접근 허용: {report_ok}")
        except PWTimeout:
            RESULTS["연구자 리포트 접근 허용"] = False
            print("  리포트 페이지 이동 실패 (타임아웃)")
        ctx3.close()

        # ── 테스트 4: 교사 계정 → 리포트 접근 차단 ─────────────────────
        print("\n[테스트 4] 교사 로그인 → 연구자 리포트 접근 차단")
        ctx4 = browser.new_context()
        p4 = ctx4.new_page()
        p4.goto(BASE)
        _wait_stable(p4)
        _login(p4, "teacher", "1234")
        try:
            p4.locator('[data-testid="stSidebarNav"] a').filter(has_text="연구자").click()
            _wait_stable(p4, 2.0)
            p4.screenshot(path="data/e2e_screenshots/v3_05_teacher_report_blocked.png")
            blocked = "연구자 전용" in p4.content() or "접근할 수 없습니다" in p4.content()
            RESULTS["교사 → 리포트 차단"] = blocked
            print(f"  리포트 차단: {blocked}")
        except PWTimeout:
            RESULTS["교사 → 리포트 차단"] = False
            print("  리포트 페이지 이동 실패 (타임아웃)")
        ctx4.close()

        # ── 테스트 5: 로그아웃 후 재차단 ────────────────────────────────
        print("\n[테스트 5] 로그아웃 후 재차단")
        ctx5 = browser.new_context()
        p5 = ctx5.new_page()
        p5.goto(BASE)
        _wait_stable(p5)
        _login(p5, "teacher", "1234")
        try:
            p5.locator('button:has-text("로그아웃")').click()
            _wait_stable(p5, 2.0)
            p5.screenshot(path="data/e2e_screenshots/v3_06_logout.png")
            form_back = p5.locator('button:has-text("로그인")').count() > 0
            RESULTS["로그아웃 후 로그인 폼 재출력"] = form_back
            print(f"  로그인 폼 재출력: {form_back}")
        except PWTimeout:
            RESULTS["로그아웃 후 로그인 폼 재출력"] = False
            print("  로그아웃 실패 (타임아웃)")
        ctx5.close()

        browser.close()

    # ── 결과 요약 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("검증 결과 요약")
    print("=" * 50)
    all_pass = True
    for name, result in RESULTS.items():
        mark = "PASS" if result else "FAIL"
        print(f"  {mark} {name}: {result}")
        if not result:
            all_pass = False
    print("=" * 50)
    print(f"최종: {'PASS' if all_pass else 'FAIL'}")
    return all_pass


if __name__ == "__main__":
    import os
    os.makedirs("data/e2e_screenshots", exist_ok=True)
    success = run_verification()
    sys.exit(0 if success else 1)
