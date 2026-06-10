"""비전 어댑터 팩토리 — 설정에 따라 어댑터를 선택하고 폴백 처리.

원칙:
- 예외를 발생시키지 않는다. 설정이 불완전하면 Mock으로 폴백.
- 반환 info에 provider / model / dry_run / fallback_reason을 포함.
- VISION_PROVIDER="external"이어도 api_key 또는 model이 없으면 Mock 폴백.
- API 키는 info dict에 포함하지 않는다.
"""

from __future__ import annotations

import logging

from core.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    VISION_API_KEY,
    VISION_DRY_RUN,
    VISION_MODEL,
    VISION_PROVIDER,
)
from services.vision.base import VisionAdapter
from services.vision.claude_adapter import ClaudeVisionAdapter
from services.vision.external_adapter import ExternalVisionAdapter
from services.vision.gemini_adapter import GeminiVisionAdapter
from services.vision.mock_adapter import MockVisionAdapter

logger = logging.getLogger(__name__)


def get_vision_adapter() -> tuple[VisionAdapter, dict]:
    """설정 기반으로 VisionAdapter 인스턴스와 상태 정보 dict를 반환한다.

    반환값:
        (adapter, info)
        info 키: provider, model, dry_run, fallback_reason
        - fallback_reason: None(정상) 또는 폴백 사유 문자열
    """
    provider = VISION_PROVIDER.lower().strip()

    if provider == "claude":
        return _try_build_claude()

    if provider == "gemini":
        return _try_build_gemini()

    if provider == "external":
        # 모델명이 claude- 로 시작하면 ClaudeVisionAdapter 자동 선택
        if VISION_MODEL.lower().startswith("claude-"):
            logger.info(
                "VISION_PROVIDER=external이지만 model=%r이 Claude 모델 — ClaudeVisionAdapter 사용",
                VISION_MODEL,
            )
            return _try_build_claude()
        return _try_build_external()

    # 기본: mock
    if provider != "mock":
        logger.warning("알 수 없는 VISION_PROVIDER=%r, mock으로 폴백합니다.", provider)

    return _mock_adapter(fallback_reason=None if provider == "mock" else f"알 수 없는 provider: {provider!r}")


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _try_build_claude() -> tuple[VisionAdapter, dict]:
    """VISION_PROVIDER=claude 시 ClaudeVisionAdapter 생성 시도.

    model 미설정 시 claude-sonnet-4-6 기본값 사용.
    api_key 없고 dry_run=False 이면 Mock 폴백.
    """
    model = VISION_MODEL or "claude-sonnet-4-6"

    if not VISION_API_KEY and not VISION_DRY_RUN:
        reason = "VISION_API_KEY / ANTHROPIC_API_KEY가 없고 dry_run=False이므로 mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    try:
        adapter = ClaudeVisionAdapter(
            model=model,
            api_key=VISION_API_KEY,
            dry_run=VISION_DRY_RUN,
        )
    except Exception as e:
        reason = f"ClaudeVisionAdapter 생성 실패({e}), mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    info = {
        "provider": "claude",
        "model": model,
        "dry_run": VISION_DRY_RUN,
        "fallback_reason": None,
    }
    logger.info("ClaudeVisionAdapter 초기화: model=%s, dry_run=%s", model, VISION_DRY_RUN)
    return adapter, info


def _try_build_external() -> tuple[VisionAdapter, dict]:
    """VISION_PROVIDER=external 시 ExternalVisionAdapter 생성 시도.

    api_key 또는 model이 없으면 MockVisionAdapter로 폴백한다.
    """
    if not VISION_MODEL:
        reason = "VISION_MODEL이 설정되지 않아 mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    if not VISION_API_KEY and not VISION_DRY_RUN:
        reason = "VISION_API_KEY가 없고 dry_run=False이므로 mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    try:
        adapter = ExternalVisionAdapter(
            model=VISION_MODEL,
            api_key=VISION_API_KEY,
            dry_run=VISION_DRY_RUN,
        )
    except Exception as e:
        reason = f"ExternalVisionAdapter 생성 실패({e}), mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    info = {
        "provider": "external",
        "model": VISION_MODEL,
        "dry_run": VISION_DRY_RUN,
        "fallback_reason": None,
    }
    logger.info(
        "ExternalVisionAdapter 초기화: model=%s, dry_run=%s",
        VISION_MODEL,
        VISION_DRY_RUN,
    )
    return adapter, info


def _try_build_gemini() -> tuple[VisionAdapter, dict]:
    """VISION_PROVIDER=gemini 시 GeminiVisionAdapter 생성 시도.

    GEMINI_API_KEY 없으면 Mock 폴백.
    클립 추출이 선행되지 않으면 analyze_segment에서 ValueError 발생.
    """
    if not GEMINI_API_KEY:
        reason = "GEMINI_API_KEY가 없으므로 mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    model = GEMINI_MODEL or "gemini-1.5-flash"
    try:
        adapter = GeminiVisionAdapter(api_key=GEMINI_API_KEY, model=model)
    except Exception as e:
        reason = f"GeminiVisionAdapter 생성 실패({e}), mock으로 폴백합니다."
        logger.warning(reason)
        return _mock_adapter(fallback_reason=reason)

    info = {
        "provider": "gemini",
        "model": model,
        "dry_run": False,
        "fallback_reason": None,
    }
    logger.info("GeminiVisionAdapter 초기화: model=%s", model)
    return adapter, info


def _mock_adapter(fallback_reason: str | None) -> tuple[VisionAdapter, dict]:
    info = {
        "provider": "mock",
        "model": "",
        "dry_run": True,
        "fallback_reason": fallback_reason,
    }
    return MockVisionAdapter(), info
