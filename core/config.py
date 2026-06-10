"""앱 공통 설정."""

import os
from pathlib import Path

# 프로젝트 루트의 .env 를 os.getenv 호출 전에 로드한다 (없으면 무시, override=False)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

DB_PATH = "data/app.db"
VIDEOS_DIR = "data/videos"                  # 원본 영상 저장 루트 (git 제외)
FRAMES_DIR = "data/frames"                  # 추출 프레임 저장 루트 (git 제외)
CLIPS_DIR  = os.getenv("CLIPS_DIR", "data/clips")  # 근거 클립 저장 루트 (git 제외)
RETENTION_DAYS = 180                        # 기본 영상 보관 기한(일)
DEFAULT_ACTOR = os.getenv("DEFAULT_ACTOR", "teacher_demo")  # 감사 로그 행위자

FRAME_BLUR_THRESHOLD = 50.0                 # Laplacian blur_score 기준 (이상이면 kept=True)
FALLBACK_SCENE_INTERVAL_SEC = 10.0          # PySceneDetect 실패 시 고정 간격 분할 기준(초)

APP_TITLE = "스마트안경 기반 유아 관찰기록 지원 시스템"
APP_CAPTION = "교사 시점 영상 분석 기반 관찰기록 초안 생성 연구용 시연 시스템"

# ---------------------------------------------------------------------------
# 비전 API 설정 — API 키는 절대 여기에 하드코딩하지 않는다 (.env 사용)
# ---------------------------------------------------------------------------
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "mock")   # "mock" | "claude" | "external"
VISION_MODEL    = os.getenv("VISION_MODEL", "")           # 예: "claude-sonnet-4-6", "gpt-4o"
VISION_DRY_RUN  = os.getenv("VISION_DRY_RUN", "true").lower() == "true"  # 기본 true (실호출 방지)
# VISION_API_KEY 없으면 ANTHROPIC_API_KEY(Claude 표준 환경변수)로 폴백
VISION_API_KEY  = os.getenv("VISION_API_KEY") or os.getenv("ANTHROPIC_API_KEY", "")  # 절대 하드코딩 금지
VISION_MAX_FRAMES_PER_SEGMENT = int(os.getenv("VISION_MAX_FRAMES_PER_SEGMENT", "4"))
VISION_MAX_IMAGE_BYTES   = int(os.getenv("VISION_MAX_IMAGE_BYTES",  str(1 * 1024 * 1024)))   # 1 MB
VISION_MAX_PAYLOAD_BYTES = int(os.getenv("VISION_MAX_PAYLOAD_BYTES", str(20 * 1024 * 1024))) # 20 MB
VISION_EXTERNAL_CONFIRM_REQUIRED = os.getenv("VISION_EXTERNAL_CONFIRM_REQUIRED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# 장면 선별 설정 — AI 분석 대상 장면/프레임 수 상한 (비용·처리 시간 통제)
# ---------------------------------------------------------------------------
VISION_SCENE_SELECTION_ENABLED = os.getenv("VISION_SCENE_SELECTION_ENABLED", "true").lower() == "true"
VISION_MAX_SCENES_PER_VIDEO    = int(os.getenv("VISION_MAX_SCENES_PER_VIDEO", "5"))
VISION_MIN_SCENE_DURATION_SEC  = float(os.getenv("VISION_MIN_SCENE_DURATION_SEC", "2.0"))
VISION_DEFAULT_MAX_SCENES_TEACHER = int(os.getenv("VISION_DEFAULT_MAX_SCENES_TEACHER", "5"))

# ---------------------------------------------------------------------------
# 근거 클립 설정 — ffmpeg 추출 구간 제어
# ---------------------------------------------------------------------------
CLIP_PADDING_SEC            = float(os.getenv("CLIP_PADDING_SEC", "2.0"))       # scene 앞뒤 여유(초)
CLIP_MIN_DURATION_SEC       = float(os.getenv("CLIP_MIN_DURATION_SEC", "4.0"))  # 클립 최소 길이
CLIP_MAX_DURATION_SEC       = float(os.getenv("CLIP_MAX_DURATION_SEC", "12.0")) # 클립 최대 길이
CLIP_MAX_CLIPS_PER_VIDEO    = int(os.getenv("CLIP_MAX_CLIPS_PER_VIDEO", "5"))   # 영상당 최대 클립 수
CLIP_MAX_TOTAL_DURATION_SEC = float(os.getenv("CLIP_MAX_TOTAL_DURATION_SEC", "60.0"))  # 총 클립 길이 상한

# ---------------------------------------------------------------------------
# Gemini API 설정 — API 키는 절대 여기에 하드코딩하지 않는다 (.env 사용)
# ---------------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")          # 절대 하드코딩 금지
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

# ---------------------------------------------------------------------------
# 인증 설정 — 비밀번호는 .env에서 관리, 코드 하드코딩 금지
# ---------------------------------------------------------------------------
TEACHER_USERS    = os.getenv("TEACHER_USERS", "teacher:1234")      # "user:pass,..." 형식
RESEARCHER_USERS = os.getenv("RESEARCHER_USERS", "researcher:1234")

# ---------------------------------------------------------------------------
# 배포·모바일 설정
# ---------------------------------------------------------------------------
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8501")  # QR코드·모바일 링크용
