"""Claude (Anthropic) 비전 어댑터 — ExternalVisionAdapter 구현체.

원칙:
- anthropic SDK를 통해 Messages API를 호출한다.
- 이미지는 base64 블록으로 전달한다. 원본 영상 경로·파일 미포함.
- 응답 텍스트에서 JSON을 추출해 반환한다.
- API 키는 절대 로그에 출력하지 않는다.
"""

from __future__ import annotations

import logging
import re

from services.vision.external_adapter import ExternalVisionAdapter

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "당신은 어린이집 교사 시점(1인칭) 스마트안경 영상에서 유아 행동을 관찰 기록하는 AI 보조 시스템입니다.\n\n"
    "역할:\n"
    "- 교사가 착용한 스마트안경 영상 프레임을 분석해 유아의 행동 관찰 후보를 제시한다.\n"
    "- 관찰 후보는 교사가 검토·수정·확정하기 위한 초안이다. AI가 확정하지 않는다.\n"
    "- 관찰수준 점수, 발달 점수, 평정 점수를 절대 포함하지 않는다.\n"
    "- 유아를 child_A, child_B 등 임시 ID로만 식별한다. 실명·확정 신원을 포함하지 않는다.\n\n"
    "출력 형식: 반드시 순수 JSON 형식으로만 출력한다. 마크다운 코드 블록, 설명 텍스트 없이 JSON만 출력한다."
)

_JSON_SCHEMA_EXAMPLE = """{
  "observations": [
    {
      "temp_child_id": "child_A",
      "observed_behavior": "관찰된 행동을 구체적으로 서술",
      "visual_evidence": "어떤 이미지에서 무엇이 보였는지 구체적으로 서술",
      "confidence": 0.75,
      "needs_teacher_review": true,
      "activity_context": "활동 맥락 (선택, 없으면 생략)",
      "peer_relation": "또래 관계 (선택, 없으면 생략)",
      "interaction": {
        "with_peers": "또래와의 상호작용 (없으면 null)",
        "with_teacher": "교사와의 상호작용 (없으면 null)",
        "with_materials": "교재·도구와의 상호작용 (없으면 null)"
      },
      "nuri_area_candidates": [
        {"area": "사회관계", "rationale": "누리과정 영역 해당 근거", "confidence": 0.7}
      ],
      "kicce_item_candidates": []
    }
  ]
}"""


class ClaudeVisionAdapter(ExternalVisionAdapter):
    """Anthropic Claude API를 사용하는 비전 어댑터.

    ExternalVisionAdapter._call_api()를 구현한다.
    payload 빌드·안전성 게이트는 부모 클래스(analyze_segment)에서 처리한다.
    """

    def _call_api(self, payload: dict) -> str:
        """Anthropic Messages API를 호출해 관찰 후보 JSON 문자열을 반환한다."""
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic 패키지가 설치되지 않았습니다. "
                "`pip install anthropic` 를 실행하세요."
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key)

        # 이미지 블록 구성 (base64, 경로 미포함)
        content: list[dict] = []
        for img in payload.get("images", []):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["mime"],
                    "data": img["data_b64"],
                },
            })

        # 텍스트 프롬프트 구성
        rules = payload.get("instruction_context", {}).get("rules", [])
        nuri_areas = payload.get("instruction_context", {}).get("nuri_areas", [])
        time_start = payload.get("time_start", 0.0)
        time_end = payload.get("time_end", 0.0)
        n_images = len(payload.get("images", []))

        text_prompt = (
            f"위 {n_images}장의 프레임은 어린이집 교사 시점 스마트안경 영상의 "
            f"{time_start:.1f}초 ~ {time_end:.1f}초 구간입니다.\n\n"
            "## 분석 규칙\n"
            + "\n".join(f"- {r}" for r in rules)
            + "\n\n## 누리과정 5개 영역 (1차 분류 기준)\n"
            + ", ".join(nuri_areas)
            + "\n\n## 출력 JSON 형식 (이 형식만 허용)\n"
            + _JSON_SCHEMA_EXAMPLE
            + "\n\n관찰 가능한 유아 행동이 없으면 `{\"observations\": []}` 를 반환하세요."
        )
        content.append({"type": "text", "text": text_prompt})

        logger.info(
            "Claude API 호출: model=%s, images=%d, segment_time=%.1f~%.1f",
            self._model, n_images, time_start, time_end,
        )

        response = client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )

        raw_text: str = response.content[0].text
        logger.debug("Claude 원시 응답 (앞 200자): %.200s", raw_text)

        return _extract_json(raw_text)


def _extract_json(text: str) -> str:
    """응답 텍스트에서 JSON 부분만 추출한다.

    Claude가 마크다운 코드 블록으로 감싸는 경우를 처리한다.
    """
    text = text.strip()

    # ```json ... ``` 또는 ``` ... ``` 형태 제거
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```$", "", text.strip())
    text = text.strip()

    # { 로 시작하는 첫 JSON 객체 추출
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return text
