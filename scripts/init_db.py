"""
로컬 개발용 SQLite DB 초기화 스크립트.

사용법:
    python scripts/init_db.py

data/app.db 가 없으면 생성하고 모든 테이블을 초기화한다.
이미 테이블이 존재하면 변경 없이 통과한다(idempotent).

주의: 실제 영상, 유아 정보, API 키는 절대 포함하지 않는다.
"""

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path 에 추가 (scripts/ 에서 직접 실행 시 필요)
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.sqlite_repository import SqliteRepository

DB_PATH = "data/app.db"


def main() -> None:
    print(f"DB 초기화: {DB_PATH}")
    repo = SqliteRepository(DB_PATH)
    repo.init_schema()
    print("완료: 모든 테이블이 준비되었습니다.")
    print(f"  위치: {Path(DB_PATH).resolve()}")


if __name__ == "__main__":
    main()
