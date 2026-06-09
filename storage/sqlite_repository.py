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
    AuditLog,
    ChildMatch,
    FinalRecord,
    Frame,
    InteractionEvidence,
    KicceItemCandidate,
    NuriAreaCandidate,
    ObservationCandidate,
    Scene,
    ScaleMappingCandidate,
    Video,
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
            retention_until TEXT
        );

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

        CREATE TABLE IF NOT EXISTS child_match (
            id             TEXT PRIMARY KEY,
            video_id       TEXT NOT NULL REFERENCES video(id),
            temp_child_id  TEXT NOT NULL,
            pseudonym_id   TEXT NOT NULL,
            matched_by     TEXT NOT NULL,
            matched_at     TEXT NOT NULL,
            UNIQUE(video_id, temp_child_id)
        );

        CREATE TABLE IF NOT EXISTS final_record (
            id                   TEXT PRIMARY KEY,
            candidate_id         TEXT NOT NULL REFERENCES observation_candidate(id),
            pseudonym_id         TEXT NOT NULL,
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
             status, created_at, retention_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                video.id, video.filename, video.stored_path,
                video.duration_sec, video.fps, video.width, video.height,
                video.status, _dt_to_str(video.created_at),
                _dt_to_str(video.retention_until),
            ))

    def get_video(self, video_id: str) -> Optional[Video]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM video WHERE id = ?", (video_id,)
            ).fetchone()
        return _row_to_video(row) if row else None

    def list_videos(self) -> list[Video]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM video ORDER BY created_at"
            ).fetchall()
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
            c = conn.execute("DELETE FROM audio_segment WHERE video_id = ?", (video_id,))
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
        sql = """
        INSERT OR REPLACE INTO child_match
            (id, video_id, temp_child_id, pseudonym_id, matched_by, matched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(sql, (
                match.id, match.video_id, match.temp_child_id,
                match.pseudonym_id, match.matched_by,
                _dt_to_str(match.matched_at),
            ))

    def list_child_matches(self, video_id: str) -> list[ChildMatch]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM child_match WHERE video_id = ? ORDER BY temp_child_id",
                (video_id,),
            ).fetchall()
        return [ChildMatch(
            id=r["id"], video_id=r["video_id"],
            temp_child_id=r["temp_child_id"], pseudonym_id=r["pseudonym_id"],
            matched_by=r["matched_by"], matched_at=_str_to_dt(r["matched_at"]),
        ) for r in rows]

    # ------------------------------------------------------------------
    # FinalRecord
    # ------------------------------------------------------------------

    def save_final_record(self, record: FinalRecord) -> None:
        sql = """
        INSERT OR REPLACE INTO final_record (
            id, candidate_id, pseudonym_id, final_behavior,
            confirmed_areas_json, confirmed_items_json,
            decision, edited, confirmed_by, confirmed_at,
            review_seconds, evidence_adequacy
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    return Video(
        id=r["id"], filename=r["filename"], stored_path=r["stored_path"],
        duration_sec=r["duration_sec"], fps=r["fps"],
        width=r["width"], height=r["height"], status=r["status"],
        created_at=_str_to_dt(r["created_at"]),
        retention_until=_str_to_dt(r["retention_until"]),
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
    )
