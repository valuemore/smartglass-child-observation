"""
SQLite 기반 Repository 구현체.

- db_path 를 생성자에서 받아 테스트 시 tmp_path 사용 가능.
- 기본 경로: data/app.db
- JSON 필드: json.dumps/loads + Pydantic model_dump/model_validate 로 직렬화.
- 점수 컬럼(score/level/rating) 없음.
- 유아 실명 필드 없음. temp_child_id / pseudonym_id 구조만 사용.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.schemas import (
    AiAssistantLog,
    AuditLog,
    Child,
    ChildMatch,
    ClassGroup,
    Clip,
    FaceMatchCandidate,
    FinalRecord,
    Frame,
    InteractionEvidence,
    KicceItemCandidate,
    NuriAreaCandidate,
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
    WeeklyDraft,
)

_DEFAULT_DB_PATH = "data/app.db"


def _dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _str_to_dt(s: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(s) if s is not None else None


class SqliteRepository:
    """SQLite 기반 저장소. Repository Protocol 을 충족한다."""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ------------------------------------------------------------------
    # 스키마 초기화
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """테이블·인덱스를 생성한다. 이미 존재하면 무시(idempotent)."""
        ddl = """
        CREATE TABLE IF NOT EXISTS class_group (
            id                 TEXT PRIMARY KEY,
            name               TEXT NOT NULL,
            teacher_owner      TEXT NOT NULL DEFAULT '',
            face_match_enabled INTEGER NOT NULL DEFAULT 0,
            created_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS child (
            id                   TEXT PRIMARY KEY,
            class_id             TEXT NOT NULL REFERENCES class_group(id),
            pseudonym_id         TEXT NOT NULL,
            display_label        TEXT NOT NULL DEFAULT '',
            reference_photo_path TEXT,
            face_embedding       BLOB,
            face_match_consent   INTEGER NOT NULL DEFAULT 0,
            consent_at           TEXT,
            consent_by           TEXT,
            created_at           TEXT NOT NULL,
            birth_date           TEXT,
            gender               TEXT,
            notes                TEXT NOT NULL DEFAULT '',
            photo_right_path     TEXT,
            photo_left_path      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_child_class ON child(class_id);

        CREATE TABLE IF NOT EXISTS video (
            id              TEXT PRIMARY KEY,
            filename        TEXT NOT NULL,
            stored_path     TEXT NOT NULL,
            duration_sec    REAL NOT NULL DEFAULT 0,
            fps             REAL NOT NULL DEFAULT 0,
            width           INTEGER NOT NULL DEFAULT 0,
            height          INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'uploaded',
            created_at      TEXT NOT NULL,
            retention_until TEXT,
            owner           TEXT NOT NULL DEFAULT '',
            institution     TEXT NOT NULL DEFAULT '',
            class_name      TEXT NOT NULL DEFAULT '',
            observation_context TEXT NOT NULL DEFAULT '',
            notes           TEXT NOT NULL DEFAULT '',
            class_id        TEXT,
            captured_date   TEXT,
            analysis_status TEXT NOT NULL DEFAULT 'queued',
            progress        INTEGER NOT NULL DEFAULT 0,
            retry_count     INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT,
            auto_analyzed   INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS clip (
            id                          TEXT PRIMARY KEY,
            video_id                    TEXT NOT NULL REFERENCES video(id),
            source_scene_ids            TEXT NOT NULL DEFAULT '[]',
            start_sec                   REAL NOT NULL DEFAULT 0,
            end_sec                     REAL NOT NULL DEFAULT 0,
            duration_sec                REAL NOT NULL DEFAULT 0,
            local_clip_path             TEXT NOT NULL DEFAULT '',
            selected_for_vision_analysis INTEGER NOT NULL DEFAULT 1,
            selection_reason            TEXT NOT NULL DEFAULT '',
            created_at                  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_clip_video ON clip(video_id);

        CREATE TABLE IF NOT EXISTS scene (
            id          TEXT PRIMARY KEY,
            video_id    TEXT NOT NULL REFERENCES video(id),
            time_start  REAL NOT NULL,
            time_end    REAL NOT NULL,
            detector    TEXT NOT NULL DEFAULT 'ContentDetector'
        );
        CREATE INDEX IF NOT EXISTS idx_scene_video ON scene(video_id);

        CREATE TABLE IF NOT EXISTS frame (
            id          TEXT PRIMARY KEY,
            scene_id    TEXT NOT NULL REFERENCES scene(id),
            t           REAL NOT NULL,
            image_path  TEXT NOT NULL,
            blur_score  REAL NOT NULL DEFAULT 0,
            kept        INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_frame_scene ON frame(scene_id);

        CREATE TABLE IF NOT EXISTS audio_segment (
            id           TEXT PRIMARY KEY,
            video_id     TEXT NOT NULL REFERENCES video(id),
            time_start   REAL NOT NULL,
            time_end     REAL NOT NULL,
            transcript   TEXT NOT NULL,
            speaker_hint TEXT NOT NULL DEFAULT 'unknown',
            is_clear     INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_audio_video ON audio_segment(video_id);

        CREATE TABLE IF NOT EXISTS observation_candidate (
            id                          TEXT PRIMARY KEY,
            video_id                    TEXT NOT NULL REFERENCES video(id),
            scene_id                    TEXT NOT NULL REFERENCES scene(id),
            time_start                  REAL NOT NULL,
            time_end                    REAL NOT NULL,
            temp_child_id               TEXT NOT NULL,
            observed_behavior           TEXT NOT NULL,
            interaction_json            TEXT,
            activity_context            TEXT,
            peer_relation               TEXT,
            visual_evidence             TEXT NOT NULL,
            audio_support               TEXT,
            nuri_area_candidates_json   TEXT NOT NULL DEFAULT '[]',
            kicce_item_candidates_json  TEXT NOT NULL DEFAULT '[]',
            confidence                  REAL NOT NULL,
            needs_teacher_review        INTEGER NOT NULL DEFAULT 1,
            created_at                  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_video ON observation_candidate(video_id);

        CREATE TABLE IF NOT EXISTS scale_mapping (
            id           TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES observation_candidate(id),
            scale        TEXT NOT NULL,
            area         TEXT,
            item_id      INTEGER,
            item_text    TEXT NOT NULL,
            rationale    TEXT NOT NULL,
            confidence   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mapping_candidate ON scale_mapping(candidate_id);

        CREATE TABLE IF NOT EXISTS face_match_candidate (
            id            TEXT PRIMARY KEY,
            video_id      TEXT NOT NULL REFERENCES video(id),
            scene_id      TEXT,
            clip_id       TEXT,
            temp_child_id TEXT NOT NULL,
            child_id      TEXT NOT NULL REFERENCES child(id),
            confidence    REAL NOT NULL DEFAULT 0,
            status        TEXT NOT NULL DEFAULT 'proposed',
            decided_by    TEXT,
            decided_at    TEXT,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_face_match_video ON face_match_candidate(video_id);

        CREATE TABLE IF NOT EXISTS child_match (
            id             TEXT PRIMARY KEY,
            video_id       TEXT NOT NULL REFERENCES video(id),
            temp_child_id  TEXT NOT NULL,
            pseudonym_id   TEXT NOT NULL,
            source         TEXT NOT NULL DEFAULT 'teacher',
            matched_by     TEXT NOT NULL,
            matched_at     TEXT NOT NULL,
            UNIQUE(video_id, temp_child_id)
        );

        CREATE TABLE IF NOT EXISTS weekly_draft (
            id                          TEXT PRIMARY KEY,
            class_id                    TEXT NOT NULL REFERENCES class_group(id),
            pseudonym_id                TEXT NOT NULL,
            period_start                TEXT NOT NULL,
            period_end                  TEXT NOT NULL,
            area                        TEXT NOT NULL,
            draft_text                  TEXT NOT NULL DEFAULT '',
            source_candidate_ids_json   TEXT NOT NULL DEFAULT '[]',
            representative_clip_ids_json TEXT NOT NULL DEFAULT '[]',
            status                      TEXT NOT NULL DEFAULT 'generated',
            created_at                  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_weekly_draft_lookup
            ON weekly_draft(class_id, pseudonym_id, period_start);

        CREATE TABLE IF NOT EXISTS final_record (
            id                   TEXT PRIMARY KEY,
            candidate_id         TEXT NOT NULL REFERENCES observation_candidate(id),
            pseudonym_id         TEXT NOT NULL,
            weekly_draft_id      TEXT,
            period_start         TEXT,
            period_end           TEXT,
            final_behavior       TEXT NOT NULL,
            confirmed_areas_json TEXT NOT NULL DEFAULT '[]',
            confirmed_items_json TEXT NOT NULL DEFAULT '[]',
            decision             TEXT NOT NULL,
            edited               INTEGER NOT NULL DEFAULT 0,
            confirmed_by         TEXT NOT NULL,
            confirmed_at         TEXT NOT NULL,
            review_seconds       INTEGER,
            evidence_adequacy    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_final_candidate ON final_record(candidate_id);

        CREATE TABLE IF NOT EXISTS ai_assistant_log (
            id               TEXT PRIMARY KEY,
            actor            TEXT NOT NULL,
            query            TEXT NOT NULL,
            intent           TEXT NOT NULL,
            response_summary TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id         TEXT PRIMARY KEY,
            video_id   TEXT NOT NULL,
            actor      TEXT NOT NULL,
            action     TEXT NOT NULL,
            detail     TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_video ON audit_log(video_id);
        """
        with self._connect() as conn:
            conn.executescript(ddl)
            # 기존 DB 마이그레이션: 없는 컬럼만 추가 (무손실)
            self._ensure_columns(conn, "final_record", {
                "review_seconds": "INTEGER",
                "evidence_adequacy": "TEXT",
            })
            self._ensure_columns(conn, "video", {
                "owner":               "TEXT NOT NULL DEFAULT ''",
                "institution":         "TEXT NOT NULL DEFAULT ''",
                "class_name":          "TEXT NOT NULL DEFAULT ''",
                "observation_context": "TEXT NOT NULL DEFAULT ''",
                "notes":               "TEXT NOT NULL DEFAULT ''",
                # V2 — 클래스 연결·매일 업로드·자동 분석 상태
                "class_id":        "TEXT",
                "captured_date":   "TEXT",
                "analysis_status": "TEXT NOT NULL DEFAULT 'queued'",
                "progress":        "INTEGER NOT NULL DEFAULT 0",
                "retry_count":     "INTEGER NOT NULL DEFAULT 0",
                "last_error":      "TEXT",
                "auto_analyzed":   "INTEGER NOT NULL DEFAULT 0",
            })
            # V2 — child_match 출처
            self._ensure_columns(conn, "child_match", {
                "source": "TEXT NOT NULL DEFAULT 'teacher'",
            })
            # V2 — final_record 주간 초안 귀속
            self._ensure_columns(conn, "final_record", {
                "weekly_draft_id": "TEXT",
                "period_start":    "TEXT",
                "period_end":      "TEXT",
            })
            # V3 — child 프로필 확장 (생년월일·성별·특이사항·우측면·좌측면 사진)
            self._ensure_columns(conn, "child", {
                "birth_date":        "TEXT",
                "gender":            "TEXT",
                "notes":             "TEXT NOT NULL DEFAULT ''",
                "photo_right_path":  "TEXT",
                "photo_left_path":   "TEXT",
            })
            # 신규 컬럼(class_id 등) 보강 후에 인덱스를 생성한다(구버전 video 호환).
            conn.execute("CREATE INDEX IF NOT EXISTS idx_video_class ON video(class_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_video_class_date ON video(class_id, captured_date)")

    @staticmethod
    def _ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
        """table에 없는 컬럼만 ALTER TABLE ADD COLUMN으로 추가한다(idempotent)."""
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    def save_video(self, video: Video) -> None:
        sql = """
        INSERT OR REPLACE INTO video
            (id, filename, stored_path, duration_sec, fps, width, height,
             status, created_at, retention_until,
             owner, institution, class_name, observation_context, notes,
             class_id, captured_date, analysis_status, progress,
             retry_count, last_error, auto_analyzed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                video.id, video.filename, video.stored_path,
                video.duration_sec, video.fps, video.width, video.height,
                video.status, _dt_to_str(video.created_at),
                _dt_to_str(video.retention_until),
                video.owner, video.institution, video.class_name,
                video.observation_context, video.notes,
                video.class_id, video.captured_date, video.analysis_status,
                video.progress, video.retry_count, video.last_error,
                int(video.auto_analyzed),
            ))

    def update_analysis_status(
        self, video_id: str, status: str, progress: int,
        last_error: Optional[str] = None,
    ) -> None:
        """업로드 즉시 자동 분석의 상태머신·진행률을 갱신한다.

        status='failed' 일 때 retry_count 를 1 증가시킨다.
        """
        with self._connect() as conn:
            if status == "failed":
                conn.execute(
                    "UPDATE video SET analysis_status = ?, progress = ?, "
                    "last_error = ?, retry_count = retry_count + 1 WHERE id = ?",
                    (status, progress, last_error, video_id),
                )
            else:
                conn.execute(
                    "UPDATE video SET analysis_status = ?, progress = ?, "
                    "last_error = ?, auto_analyzed = ? WHERE id = ?",
                    (status, progress, last_error, int(status == "done"), video_id),
                )

    def get_video(self, video_id: str) -> Optional[Video]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM video WHERE id = ?", (video_id,)
            ).fetchone()
        return _row_to_video(row) if row else None

    def list_videos(self, owner: Optional[str] = None) -> list[Video]:
        """영상 목록 조회. owner가 None이면 전체, 값이 있으면 해당 owner의 영상만."""
        if owner is not None:
            sql = "SELECT * FROM video WHERE owner = ? ORDER BY created_at"
            params: tuple = (owner,)
        else:
            sql = "SELECT * FROM video ORDER BY created_at"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_video(r) for r in rows]

    def delete_video_cascade(self, video_id: str) -> int:
        """video와 연관 행 전체를 cascade 삭제한다. audit_log는 보존."""
        total = 0
        with self._connect() as conn:
            c = conn.execute(
                "DELETE FROM scale_mapping WHERE candidate_id IN "
                "(SELECT id FROM observation_candidate WHERE video_id = ?)",
                (video_id,),
            )
            total += c.rowcount
            c = conn.execute(
                "DELETE FROM final_record WHERE candidate_id IN "
                "(SELECT id FROM observation_candidate WHERE video_id = ?)",
                (video_id,),
            )
            total += c.rowcount
            c = conn.execute(
                "DELETE FROM observation_candidate WHERE video_id = ?", (video_id,)
            )
            total += c.rowcount
            c = conn.execute(
                "DELETE FROM frame WHERE scene_id IN "
                "(SELECT id FROM scene WHERE video_id = ?)",
                (video_id,),
            )
            total += c.rowcount
            c = conn.execute("DELETE FROM scene WHERE video_id = ?", (video_id,))
            total += c.rowcount
            c = conn.execute("DELETE FROM child_match WHERE video_id = ?", (video_id,))
            total += c.rowcount
            c = conn.execute("DELETE FROM face_match_candidate WHERE video_id = ?", (video_id,))
            total += c.rowcount
            c = conn.execute("DELETE FROM audio_segment WHERE video_id = ?", (video_id,))
            total += c.rowcount
            c = conn.execute("DELETE FROM clip WHERE video_id = ?", (video_id,))
            total += c.rowcount
            c = conn.execute("DELETE FROM video WHERE id = ?", (video_id,))
            total += c.rowcount
        return total

    # ------------------------------------------------------------------
    # Scene
    # ------------------------------------------------------------------

    def add_scenes(self, scenes: list[Scene]) -> None:
        sql = """
        INSERT OR IGNORE INTO scene (id, video_id, time_start, time_end, detector)
        VALUES (?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.executemany(sql, [
                (s.id, s.video_id, s.time_start, s.time_end, s.detector)
                for s in scenes
            ])

    def list_scenes(self, video_id: str) -> list[Scene]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scene WHERE video_id = ? ORDER BY time_start",
                (video_id,),
            ).fetchall()
        return [Scene(
            id=r["id"], video_id=r["video_id"],
            time_start=r["time_start"], time_end=r["time_end"],
            detector=r["detector"],
        ) for r in rows]

    # ------------------------------------------------------------------
    # Frame
    # ------------------------------------------------------------------

    def add_frames(self, frames: list[Frame]) -> None:
        # ON CONFLICT: kept 값만 갱신 — 재전처리 시 fallback 결과가 DB에 반영되어야 한다.
        sql = """
        INSERT INTO frame (id, scene_id, t, image_path, blur_score, kept)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET kept = excluded.kept
        """
        with self._connect() as conn:
            conn.executemany(sql, [
                (f.id, f.scene_id, f.t, f.image_path, f.blur_score, int(f.kept))
                for f in frames
            ])

    def list_frames(self, scene_id: str) -> list[Frame]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM frame WHERE scene_id = ? ORDER BY t",
                (scene_id,),
            ).fetchall()
        return [Frame(
            id=r["id"], scene_id=r["scene_id"], t=r["t"],
            image_path=r["image_path"], blur_score=r["blur_score"],
            kept=bool(r["kept"]),
        ) for r in rows]

    # ------------------------------------------------------------------
    # Clip — 근거 클립
    # ------------------------------------------------------------------

    def add_clips(self, clips: list[Clip]) -> None:
        sql = """
        INSERT OR IGNORE INTO clip
            (id, video_id, source_scene_ids, start_sec, end_sec, duration_sec,
             local_clip_path, selected_for_vision_analysis, selection_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.executemany(sql, [
                (c.id, c.video_id, json.dumps(c.source_scene_ids),
                 c.start_sec, c.end_sec, c.duration_sec,
                 c.local_clip_path, int(c.selected_for_vision_analysis),
                 c.selection_reason, _dt_to_str(c.created_at))
                for c in clips
            ])

    def update_clip_path(self, clip_id: str, local_clip_path: str) -> None:
        """ffmpeg 추출 완료 후 클립 경로를 업데이트한다."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE clip SET local_clip_path = ? WHERE id = ?",
                (local_clip_path, clip_id),
            )

    def list_clips(self, video_id: str) -> list[Clip]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clip WHERE video_id = ? ORDER BY start_sec",
                (video_id,),
            ).fetchall()
        return [_row_to_clip(r) for r in rows]

    def get_clips_for_scene(self, scene_id: str, video_id: str) -> list[Clip]:
        """주어진 scene_id가 source_scene_ids에 포함된 클립 목록을 반환한다."""
        return [c for c in self.list_clips(video_id) if scene_id in c.source_scene_ids]

    # ------------------------------------------------------------------
    # ObservationCandidate
    # ------------------------------------------------------------------

    def add_candidates(self, candidates: list[ObservationCandidate]) -> None:
        sql = """
        INSERT OR IGNORE INTO observation_candidate (
            id, video_id, scene_id, time_start, time_end, temp_child_id,
            observed_behavior, interaction_json, activity_context,
            peer_relation, visual_evidence, audio_support,
            nuri_area_candidates_json, kicce_item_candidates_json,
            confidence, needs_teacher_review, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = []
        for c in candidates:
            interaction_json = (
                json.dumps(c.interaction.model_dump(mode="json"))
                if c.interaction else None
            )
            nuri_json = json.dumps([n.model_dump(mode="json") for n in c.nuri_area_candidates])
            kicce_json = json.dumps([k.model_dump(mode="json") for k in c.kicce_item_candidates])
            rows.append((
                c.id, c.video_id, c.scene_id, c.time_start, c.time_end,
                c.temp_child_id, c.observed_behavior, interaction_json,
                c.activity_context, c.peer_relation, c.visual_evidence,
                c.audio_support, nuri_json, kicce_json,
                c.confidence, int(c.needs_teacher_review),
                _dt_to_str(c.created_at),
            ))
        with self._connect() as conn:
            conn.executemany(sql, rows)

    def get_candidate(self, candidate_id: str) -> Optional[ObservationCandidate]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM observation_candidate WHERE id = ?", (candidate_id,)
            ).fetchone()
        return _row_to_candidate(row) if row else None

    def list_candidates(self, video_id: str) -> list[ObservationCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM observation_candidate WHERE video_id = ? ORDER BY time_start",
                (video_id,),
            ).fetchall()
        return [_row_to_candidate(r) for r in rows]

    def delete_candidates_for_video(self, video_id: str) -> None:
        # 연결된 scale_mapping 을 먼저 삭제한 뒤 관찰 후보를 삭제한다(고아 매핑 방지).
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM scale_mapping
                WHERE candidate_id IN (
                    SELECT id FROM observation_candidate WHERE video_id = ?
                )
                """,
                (video_id,),
            )
            conn.execute(
                "DELETE FROM observation_candidate WHERE video_id = ?",
                (video_id,),
            )

    # ------------------------------------------------------------------
    # ScaleMappingCandidate
    # ------------------------------------------------------------------

    def add_mappings(self, mappings: list[ScaleMappingCandidate]) -> None:
        sql = """
        INSERT OR IGNORE INTO scale_mapping
            (id, candidate_id, scale, area, item_id, item_text, rationale, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.executemany(sql, [
                (m.id, m.candidate_id, m.scale, m.area, m.item_id,
                 m.item_text, m.rationale, m.confidence)
                for m in mappings
            ])

    def list_mappings(self, candidate_id: str) -> list[ScaleMappingCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scale_mapping WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchall()
        return [ScaleMappingCandidate(
            id=r["id"], candidate_id=r["candidate_id"], scale=r["scale"],
            area=r["area"], item_id=r["item_id"], item_text=r["item_text"],
            rationale=r["rationale"], confidence=r["confidence"],
        ) for r in rows]

    def delete_mappings_for_candidate(self, candidate_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM scale_mapping WHERE candidate_id = ?",
                (candidate_id,),
            )

    # ------------------------------------------------------------------
    # ChildMatch
    # ------------------------------------------------------------------

    def set_child_match(self, match: ChildMatch) -> None:
        # UNIQUE(video_id, temp_child_id) 충돌 시 갱신. id 가 달라도 동일 매칭으로 본다.
        sql = """
        INSERT INTO child_match
            (id, video_id, temp_child_id, pseudonym_id, source, matched_by, matched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id, temp_child_id) DO UPDATE SET
            id = excluded.id,
            pseudonym_id = excluded.pseudonym_id,
            source = excluded.source,
            matched_by = excluded.matched_by,
            matched_at = excluded.matched_at
        """
        with self._connect() as conn:
            conn.execute(sql, (
                match.id, match.video_id, match.temp_child_id,
                match.pseudonym_id, match.source, match.matched_by,
                _dt_to_str(match.matched_at),
            ))

    def list_child_matches(self, video_id: str) -> list[ChildMatch]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM child_match WHERE video_id = ? ORDER BY temp_child_id",
                (video_id,),
            ).fetchall()
        keys_present = [set(r.keys()) for r in rows]
        return [ChildMatch(
            id=r["id"], video_id=r["video_id"],
            temp_child_id=r["temp_child_id"], pseudonym_id=r["pseudonym_id"],
            source=(r["source"] if "source" in k else "teacher"),
            matched_by=r["matched_by"], matched_at=_str_to_dt(r["matched_at"]),
        ) for r, k in zip(rows, keys_present)]

    # ------------------------------------------------------------------
    # FinalRecord
    # ------------------------------------------------------------------

    def save_final_record(self, record: FinalRecord) -> None:
        sql = """
        INSERT OR REPLACE INTO final_record (
            id, candidate_id, pseudonym_id, final_behavior,
            confirmed_areas_json, confirmed_items_json,
            decision, edited, confirmed_by, confirmed_at,
            review_seconds, evidence_adequacy,
            weekly_draft_id, period_start, period_end
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        areas_json = json.dumps(record.confirmed_areas)
        items_json = json.dumps([i.model_dump(mode="json") for i in record.confirmed_items])
        with self._connect() as conn:
            conn.execute(sql, (
                record.id, record.candidate_id, record.pseudonym_id,
                record.final_behavior, areas_json, items_json,
                record.decision, int(record.edited),
                record.confirmed_by, _dt_to_str(record.confirmed_at),
                record.review_seconds, record.evidence_adequacy,
                record.weekly_draft_id, record.period_start, record.period_end,
            ))

    def list_final_records(self, video_id: Optional[str] = None) -> list[FinalRecord]:
        if video_id is not None:
            sql = """
            SELECT fr.* FROM final_record fr
            JOIN observation_candidate oc ON fr.candidate_id = oc.id
            WHERE oc.video_id = ?
            ORDER BY fr.confirmed_at
            """
            params: tuple = (video_id,)
        else:
            sql = "SELECT * FROM final_record ORDER BY confirmed_at"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_final_record(r) for r in rows]

    # ------------------------------------------------------------------
    # ClassGroup — 학급
    # ------------------------------------------------------------------

    def save_class(self, group: ClassGroup) -> None:
        sql = """
        INSERT OR REPLACE INTO class_group
            (id, name, teacher_owner, face_match_enabled, created_at)
        VALUES (?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                group.id, group.name, group.teacher_owner,
                int(group.face_match_enabled), _dt_to_str(group.created_at),
            ))

    def get_class(self, class_id: str) -> Optional[ClassGroup]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM class_group WHERE id = ?", (class_id,)
            ).fetchone()
        return _row_to_class(row) if row else None

    def list_classes(self, teacher_owner: Optional[str] = None) -> list[ClassGroup]:
        if teacher_owner is not None:
            sql = "SELECT * FROM class_group WHERE teacher_owner = ? ORDER BY created_at"
            params: tuple = (teacher_owner,)
        else:
            sql = "SELECT * FROM class_group ORDER BY created_at"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_class(r) for r in rows]

    # ------------------------------------------------------------------
    # Child — 등록 유아 (실명 미저장, 얼굴 데이터는 동의 시에만)
    # ------------------------------------------------------------------

    def add_child(self, child: Child) -> None:
        sql = """
        INSERT OR REPLACE INTO child
            (id, class_id, pseudonym_id, display_label, reference_photo_path,
             face_embedding, face_match_consent, consent_at, consent_by, created_at,
             birth_date, gender, notes, photo_right_path, photo_left_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                child.id, child.class_id, child.pseudonym_id, child.display_label,
                child.reference_photo_path, child.face_embedding,
                int(child.face_match_consent),
                _dt_to_str(child.consent_at), child.consent_by,
                _dt_to_str(child.created_at),
                child.birth_date, child.gender, child.notes,
                child.photo_right_path, child.photo_left_path,
            ))

    def get_child(self, child_id: str) -> Optional[Child]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM child WHERE id = ?", (child_id,)
            ).fetchone()
        return _row_to_child(row) if row else None

    def list_children(self, class_id: str) -> list[Child]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM child WHERE class_id = ? ORDER BY pseudonym_id",
                (class_id,),
            ).fetchall()
        return [_row_to_child(r) for r in rows]

    def update_child_meta(
        self,
        child_id: str,
        display_label: str,
        birth_date: Optional[str],
        gender: Optional[str],
        notes: str,
    ) -> None:
        """display_label, birth_date, gender, notes를 갱신한다. 사진·동의는 별도 메서드."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE child SET display_label = ?, birth_date = ?, gender = ?, notes = ? WHERE id = ?",
                (display_label, birth_date, gender, notes, child_id),
            )

    def update_child_photos(
        self,
        child_id: str,
        photo_right_path: Optional[str],
        photo_left_path: Optional[str],
    ) -> None:
        """우측면·좌측면 사진 경로를 갱신하고 임베딩을 무효화한다(다각도 평균 재계산 유도)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE child SET photo_right_path = ?, photo_left_path = ?, "
                "face_embedding = NULL WHERE id = ? AND face_match_consent = 1",
                (photo_right_path, photo_left_path, child_id),
            )

    def set_face_consent(
        self, child_id: str, consent: bool, by: Optional[str] = None,
        consent_at: Optional[datetime] = None,
    ) -> None:
        """얼굴매칭 동의 상태를 변경한다. 철회(consent=False) 시 참조사진·임베딩을 즉시 삭제한다.

        반환: 없음. 참조사진 파일 삭제는 호출측(security 서비스)이 수행하고 감사 로그를 남긴다.
        """
        with self._connect() as conn:
            if consent:
                conn.execute(
                    "UPDATE child SET face_match_consent = 1, consent_at = ?, consent_by = ? WHERE id = ?",
                    (_dt_to_str(consent_at or datetime.now()), by, child_id),
                )
            else:
                # 동의 철회: 임베딩·참조사진 경로를 즉시 비운다(보안 불변식).
                conn.execute(
                    "UPDATE child SET face_match_consent = 0, reference_photo_path = NULL, "
                    "face_embedding = NULL, photo_right_path = NULL, photo_left_path = NULL, "
                    "consent_at = ?, consent_by = ? WHERE id = ?",
                    (_dt_to_str(consent_at or datetime.now()), by, child_id),
                )

    def set_child_embedding(self, child_id: str, embedding: Optional[bytes]) -> None:
        """유아의 얼굴 임베딩을 저장한다. **동의(face_match_consent=1)한 유아만** 갱신된다(보안 가드)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE child SET face_embedding = ? WHERE id = ? AND face_match_consent = 1",
                (embedding, child_id),
            )

    def delete_child_cascade(self, child_id: str) -> int:
        """유아와 연관 얼굴 매칭 후보를 삭제한다(참조사진·임베딩 포함). audit_log는 보존."""
        total = 0
        with self._connect() as conn:
            c = conn.execute(
                "DELETE FROM face_match_candidate WHERE child_id = ?", (child_id,)
            )
            total += c.rowcount
            c = conn.execute("DELETE FROM child WHERE id = ?", (child_id,))
            total += c.rowcount
        return total

    # ------------------------------------------------------------------
    # FaceMatchCandidate — 얼굴 참조 매칭 후보 (AI 제시, 교사 확정)
    # ------------------------------------------------------------------

    def add_face_match_candidates(self, items: list[FaceMatchCandidate]) -> None:
        sql = """
        INSERT OR IGNORE INTO face_match_candidate
            (id, video_id, scene_id, clip_id, temp_child_id, child_id,
             confidence, status, decided_by, decided_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.executemany(sql, [
                (f.id, f.video_id, f.scene_id, f.clip_id, f.temp_child_id,
                 f.child_id, f.confidence, f.status, f.decided_by,
                 _dt_to_str(f.decided_at), _dt_to_str(f.created_at))
                for f in items
            ])

    def list_face_match_candidates(self, video_id: str) -> list[FaceMatchCandidate]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM face_match_candidate WHERE video_id = ? "
                "ORDER BY temp_child_id, confidence DESC",
                (video_id,),
            ).fetchall()
        return [_row_to_face_match(r) for r in rows]

    def decide_face_match(
        self, candidate_id: str, status: str, decided_by: str,
        decided_at: Optional[datetime] = None,
    ) -> None:
        """교사가 얼굴 매칭 후보를 확정/기각한다. AI는 이 메서드를 호출하지 않는다."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE face_match_candidate SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                (status, decided_by, _dt_to_str(decided_at or datetime.now()), candidate_id),
            )

    def delete_face_match_candidates_for_video(
        self, video_id: str, only_proposed: bool = True,
    ) -> int:
        """영상의 얼굴 매칭 후보를 삭제한다. 기본은 교사 미결정(proposed)만 삭제해
        확정/기각(confirmed/rejected) 결과를 보존한다(재분석 멱등성)."""
        with self._connect() as conn:
            if only_proposed:
                c = conn.execute(
                    "DELETE FROM face_match_candidate WHERE video_id = ? AND status = 'proposed'",
                    (video_id,),
                )
            else:
                c = conn.execute(
                    "DELETE FROM face_match_candidate WHERE video_id = ?", (video_id,),
                )
            return c.rowcount

    # ------------------------------------------------------------------
    # WeeklyDraft — 주간/격주 관찰기록 초안
    # ------------------------------------------------------------------

    def save_weekly_drafts(self, drafts: list[WeeklyDraft]) -> None:
        sql = """
        INSERT OR REPLACE INTO weekly_draft
            (id, class_id, pseudonym_id, period_start, period_end, area,
             draft_text, source_candidate_ids_json, representative_clip_ids_json,
             status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.executemany(sql, [
                (d.id, d.class_id, d.pseudonym_id, d.period_start, d.period_end,
                 d.area, d.draft_text, json.dumps(d.source_candidate_ids),
                 json.dumps(d.representative_clip_ids), d.status,
                 _dt_to_str(d.created_at))
                for d in drafts
            ])

    def get_weekly_draft(self, draft_id: str) -> Optional[WeeklyDraft]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM weekly_draft WHERE id = ?", (draft_id,)
            ).fetchone()
        return _row_to_weekly_draft(row) if row else None

    def list_weekly_drafts(
        self, class_id: str, pseudonym_id: Optional[str] = None,
    ) -> list[WeeklyDraft]:
        if pseudonym_id is not None:
            sql = ("SELECT * FROM weekly_draft WHERE class_id = ? AND pseudonym_id = ? "
                   "ORDER BY period_start, area")
            params: tuple = (class_id, pseudonym_id)
        else:
            sql = "SELECT * FROM weekly_draft WHERE class_id = ? ORDER BY period_start, pseudonym_id, area"
            params = (class_id,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_weekly_draft(r) for r in rows]

    def update_draft_status(self, draft_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE weekly_draft SET status = ? WHERE id = ?", (status, draft_id)
            )

    # ------------------------------------------------------------------
    # AiAssistantLog — AI비서 감사
    # ------------------------------------------------------------------

    def write_assistant_log(self, entry: AiAssistantLog) -> None:
        sql = """
        INSERT INTO ai_assistant_log (id, actor, query, intent, response_summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                entry.id, entry.actor, entry.query, entry.intent,
                entry.response_summary, _dt_to_str(entry.created_at),
            ))

    def list_assistant_logs(self, actor: Optional[str] = None) -> list[AiAssistantLog]:
        if actor is not None:
            sql = "SELECT * FROM ai_assistant_log WHERE actor = ? ORDER BY created_at"
            params: tuple = (actor,)
        else:
            sql = "SELECT * FROM ai_assistant_log ORDER BY created_at"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [AiAssistantLog(
            id=r["id"], actor=r["actor"], query=r["query"], intent=r["intent"],
            response_summary=r["response_summary"], created_at=_str_to_dt(r["created_at"]),
        ) for r in rows]

    # ------------------------------------------------------------------
    # AuditLog
    # ------------------------------------------------------------------

    def write_audit(self, entry: AuditLog) -> None:
        sql = """
        INSERT INTO audit_log (id, video_id, actor, action, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                entry.id, entry.video_id, entry.actor, entry.action,
                entry.detail, _dt_to_str(entry.created_at),
            ))

    def list_audit_logs(self, video_id: Optional[str] = None) -> list[AuditLog]:
        if video_id is not None:
            sql = "SELECT * FROM audit_log WHERE video_id = ? ORDER BY created_at"
            params: tuple = (video_id,)
        else:
            sql = "SELECT * FROM audit_log ORDER BY created_at"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [AuditLog(
            id=r["id"], video_id=r["video_id"], actor=r["actor"],
            action=r["action"], detail=r["detail"],
            created_at=_str_to_dt(r["created_at"]),
        ) for r in rows]


# ---------------------------------------------------------------------------
# 행 → Pydantic 변환 헬퍼
# ---------------------------------------------------------------------------

def _row_to_video(r: sqlite3.Row) -> Video:
    keys = set(r.keys())
    return Video(
        id=r["id"], filename=r["filename"], stored_path=r["stored_path"],
        duration_sec=r["duration_sec"], fps=r["fps"],
        width=r["width"], height=r["height"], status=r["status"],
        created_at=_str_to_dt(r["created_at"]),
        retention_until=_str_to_dt(r["retention_until"]),
        owner=r["owner"] if "owner" in keys else "",
        institution=r["institution"] if "institution" in keys else "",
        class_name=r["class_name"] if "class_name" in keys else "",
        observation_context=r["observation_context"] if "observation_context" in keys else "",
        notes=r["notes"] if "notes" in keys else "",
        class_id=r["class_id"] if "class_id" in keys else None,
        captured_date=r["captured_date"] if "captured_date" in keys else None,
        analysis_status=r["analysis_status"] if "analysis_status" in keys else "queued",
        progress=r["progress"] if "progress" in keys else 0,
        retry_count=r["retry_count"] if "retry_count" in keys else 0,
        last_error=r["last_error"] if "last_error" in keys else None,
        auto_analyzed=bool(r["auto_analyzed"]) if "auto_analyzed" in keys else False,
    )


def _row_to_class(r: sqlite3.Row) -> ClassGroup:
    return ClassGroup(
        id=r["id"], name=r["name"], teacher_owner=r["teacher_owner"],
        face_match_enabled=bool(r["face_match_enabled"]),
        created_at=_str_to_dt(r["created_at"]),
    )


def _row_to_child(r: sqlite3.Row) -> Child:
    keys = set(r.keys())
    return Child(
        id=r["id"], class_id=r["class_id"], pseudonym_id=r["pseudonym_id"],
        display_label=r["display_label"] or "",
        reference_photo_path=r["reference_photo_path"],
        face_embedding=r["face_embedding"],
        face_match_consent=bool(r["face_match_consent"]),
        consent_at=_str_to_dt(r["consent_at"]),
        consent_by=r["consent_by"],
        created_at=_str_to_dt(r["created_at"]),
        birth_date=r["birth_date"] if "birth_date" in keys else None,
        gender=r["gender"] if "gender" in keys else None,
        notes=r["notes"] if "notes" in keys else "",
        photo_right_path=r["photo_right_path"] if "photo_right_path" in keys else None,
        photo_left_path=r["photo_left_path"] if "photo_left_path" in keys else None,
    )


def _row_to_face_match(r: sqlite3.Row) -> FaceMatchCandidate:
    return FaceMatchCandidate(
        id=r["id"], video_id=r["video_id"],
        scene_id=r["scene_id"], clip_id=r["clip_id"],
        temp_child_id=r["temp_child_id"], child_id=r["child_id"],
        confidence=r["confidence"], status=r["status"],
        decided_by=r["decided_by"], decided_at=_str_to_dt(r["decided_at"]),
        created_at=_str_to_dt(r["created_at"]),
    )


def _row_to_weekly_draft(r: sqlite3.Row) -> WeeklyDraft:
    return WeeklyDraft(
        id=r["id"], class_id=r["class_id"], pseudonym_id=r["pseudonym_id"],
        period_start=r["period_start"], period_end=r["period_end"],
        area=r["area"], draft_text=r["draft_text"] or "",
        source_candidate_ids=json.loads(r["source_candidate_ids_json"] or "[]"),
        representative_clip_ids=json.loads(r["representative_clip_ids_json"] or "[]"),
        status=r["status"], created_at=_str_to_dt(r["created_at"]),
    )


def _row_to_clip(r: sqlite3.Row) -> Clip:
    return Clip(
        id=r["id"], video_id=r["video_id"],
        source_scene_ids=json.loads(r["source_scene_ids"] or "[]"),
        start_sec=r["start_sec"], end_sec=r["end_sec"], duration_sec=r["duration_sec"],
        local_clip_path=r["local_clip_path"] or "",
        selected_for_vision_analysis=bool(r["selected_for_vision_analysis"]),
        selection_reason=r["selection_reason"] or "",
        created_at=_str_to_dt(r["created_at"]),
    )


def _row_to_candidate(r: sqlite3.Row) -> ObservationCandidate:
    interaction = None
    if r["interaction_json"]:
        interaction = InteractionEvidence.model_validate(json.loads(r["interaction_json"]))

    nuri = [
        NuriAreaCandidate.model_validate(item)
        for item in json.loads(r["nuri_area_candidates_json"] or "[]")
    ]
    kicce = [
        KicceItemCandidate.model_validate(item)
        for item in json.loads(r["kicce_item_candidates_json"] or "[]")
    ]
    return ObservationCandidate(
        id=r["id"], video_id=r["video_id"], scene_id=r["scene_id"],
        time_start=r["time_start"], time_end=r["time_end"],
        temp_child_id=r["temp_child_id"],
        observed_behavior=r["observed_behavior"],
        interaction=interaction,
        activity_context=r["activity_context"],
        peer_relation=r["peer_relation"],
        visual_evidence=r["visual_evidence"],
        audio_support=r["audio_support"],
        nuri_area_candidates=nuri,
        kicce_item_candidates=kicce,
        confidence=r["confidence"],
        needs_teacher_review=bool(r["needs_teacher_review"]),
        created_at=_str_to_dt(r["created_at"]),
    )


def _row_to_final_record(r: sqlite3.Row) -> FinalRecord:
    areas: list[str] = json.loads(r["confirmed_areas_json"] or "[]")
    items = [
        KicceItemCandidate.model_validate(item)
        for item in json.loads(r["confirmed_items_json"] or "[]")
    ]
    keys = set(r.keys())
    review_seconds = r["review_seconds"] if "review_seconds" in keys else None
    evidence_adequacy = r["evidence_adequacy"] if "evidence_adequacy" in keys else None
    return FinalRecord(
        id=r["id"], candidate_id=r["candidate_id"],
        pseudonym_id=r["pseudonym_id"], final_behavior=r["final_behavior"],
        confirmed_areas=areas, confirmed_items=items,
        decision=r["decision"], edited=bool(r["edited"]),
        confirmed_by=r["confirmed_by"],
        confirmed_at=_str_to_dt(r["confirmed_at"]),
        review_seconds=review_seconds,
        evidence_adequacy=evidence_adequacy,
        weekly_draft_id=r["weekly_draft_id"] if "weekly_draft_id" in keys else None,
        period_start=r["period_start"] if "period_start" in keys else None,
        period_end=r["period_end"] if "period_end" in keys else None,
    )
