"""앱 공통 설정."""

DB_PATH = "data/app.db"
VIDEOS_DIR = "data/videos"                  # 원본 영상 저장 루트 (git 제외)
FRAMES_DIR = "data/frames"                  # 추출 프레임 저장 루트 (git 제외)
RETENTION_DAYS = 180                        # 기본 영상 보관 기한(일)
DEFAULT_ACTOR = "teacher_demo"              # 1차 시연용 감사 로그 행위자

BLUR_THRESHOLD = 80.0                       # Laplacian blur_score 기준 (이상이면 kept=True)
FALLBACK_SCENE_INTERVAL_SEC = 10.0          # PySceneDetect 실패 시 고정 간격 분할 기준(초)

APP_TITLE = "스마트안경 기반 유아 관찰기록 지원 시스템"
APP_CAPTION = "교사 시점 영상 분석 기반 관찰기록 초안 생성 연구용 시연 시스템"
