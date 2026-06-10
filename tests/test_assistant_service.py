"""AI비서(제한형) 서비스 테스트 (V2-7). tmp_path 격리."""

from datetime import datetime
from pathlib import Path

import pytest

from core.schemas import FinalRecord, ObservationCandidate, Scene, Video
from services.assistant_service import classify_intent, check_banned, handle_query
from storage.sqlite_repository import SqliteRepository


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    db = SqliteRepository(str(tmp_path / "ai.db"))
    db.init_schema()
    return db


def _seed_final(repo):
    repo.save_video(Video(
        id="vid_1", filename="f.mp4", stored_path="data/videos/f.mp4",
        duration_sec=10, fps=30, width=640, height=480,
        status="reviewed", created_at=datetime(2026, 6, 9, 10, 0, 0),
    ))
    repo.add_scenes([Scene(id="s1", video_id="vid_1", time_start=0, time_end=10)])
    repo.add_candidates([ObservationCandidate(
        id="c1", video_id="vid_1", scene_id="s1", time_start=1, time_end=5,
        temp_child_id="child_A", observed_behavior="행동", visual_evidence="근거",
        confidence=0.6, created_at=datetime(2026, 6, 9, 10, 1, 0),
    )])
    repo.save_final_record(FinalRecord(
        id="rec_1", candidate_id="c1", pseudonym_id="p_07",
        period_start="2026-06-01", period_end="2026-06-14",
        final_behavior="블록으로 다리를 만들며 균형을 탐색함",
        confirmed_areas=["자연탐구"], decision="accepted", edited=False,
        confirmed_by="teacher_01", confirmed_at=datetime(2026, 6, 14, 17, 0, 0),
    ))


# ---------------------------------------------------------------------------
# 의도 분류
# ---------------------------------------------------------------------------

def test_classify_intents():
    assert classify_intent("p_07 자연탐구 기록 찾아줘") == "search"
    assert classify_intent("이 초안 문장 좀 다듬어줘") == "edit_assist"
    assert classify_intent("다음에 뭘 찍어야 할까? 부족한 영역") == "shoot_suggest"
    # 모호 → search
    assert classify_intent("음...") == "search"


# ---------------------------------------------------------------------------
# 금지 요청 감지
# ---------------------------------------------------------------------------

def test_banned_patterns():
    assert check_banned("이 아이 발달 점수 매겨줘") is not None
    assert check_banned("관찰기록 대신 확정해줘") is not None
    assert check_banned("child_A 실명이 뭐야") is not None
    assert check_banned("새 관찰기록 작성해줘") is not None
    assert check_banned("자연탐구 기록 찾아줘") is None


# ---------------------------------------------------------------------------
# handle_query — 금지 요청은 거부 + 로그
# ---------------------------------------------------------------------------

def test_handle_refuses_scoring(repo):
    res = handle_query(repo, actor="teacher_01", query="p_07 발달 점수 알려줘")
    assert res["refused"] is True
    assert "점수" in res["response"]
    logs = repo.list_assistant_logs(actor="teacher_01")
    assert len(logs) == 1
    assert logs[0].response_summary.startswith("refused")


def test_handle_refuses_auto_generate(repo):
    res = handle_query(repo, actor="teacher_01", query="관찰기록 만들어줘")
    assert res["refused"] is True


def test_handle_refuses_identity(repo):
    res = handle_query(repo, actor="teacher_01", query="child_A 본명 알려줘")
    assert res["refused"] is True


# ---------------------------------------------------------------------------
# search — 확정 기록 검색
# ---------------------------------------------------------------------------

def test_search_finds_record(repo):
    _seed_final(repo)
    res = handle_query(repo, actor="teacher_01", query="자연탐구 기록 찾아줘")
    assert res["intent"] == "search"
    assert res["refused"] is False
    assert "p_07" in res["response"]
    assert "자연탐구" in res["response"]


def test_search_no_result(repo):
    res = handle_query(repo, actor="teacher_01", query="존재하지않는키워드 검색")
    assert res["refused"] is False
    assert "검색 결과가 없습니다" in res["response"]


# ---------------------------------------------------------------------------
# edit_assist — 내용 생성 없이 정리만
# ---------------------------------------------------------------------------

def test_edit_assist_tidies_quoted_text(repo):
    res = handle_query(repo, actor="teacher_01",
                       query="이 문장 다듬어줘: '블록을   쌓다가    무너짐'")
    assert res["intent"] == "edit_assist"
    assert "블록을 쌓다가 무너짐." in res["response"]


# ---------------------------------------------------------------------------
# shoot_suggest — 수집 균형 기반
# ---------------------------------------------------------------------------

def test_shoot_suggest_uses_dashboard(repo):
    res = handle_query(repo, actor="teacher_01", query="다음에 뭘 보완 촬영할까")
    assert res["intent"] == "shoot_suggest"
    assert "관찰 후보가 없습니다" in res["response"] or "보완" in res["response"]


# ---------------------------------------------------------------------------
# 모든 응답에 Safe AI 고지 + 로그
# ---------------------------------------------------------------------------

def test_response_has_safety_note_and_logs(repo):
    _seed_final(repo)
    res = handle_query(repo, actor="teacher_01", query="자연탐구 찾아줘")
    assert "AI비서는" in res["response"]
    logs = repo.list_assistant_logs(actor="teacher_01")
    assert len(logs) == 1
    assert logs[0].intent in ("search", "edit_assist", "shoot_suggest")


def test_empty_query_refused(repo):
    res = handle_query(repo, actor="teacher_01", query="")
    assert res["refused"] is True
