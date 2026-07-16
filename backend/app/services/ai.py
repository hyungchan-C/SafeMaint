from openai import OpenAI

from app.core.config import settings


SYSTEM_INSTRUCTIONS = """당신은 SafeMaint AI의 산업안전 보조자입니다.
한국어로 명확하고 간결하게 답하세요. 작업 전 위험요인, 에너지 차단(LOTO), 필요한 보호구와
확인 절차를 우선 안내하세요. 제공되지 않은 법령, 매뉴얼 내용이나 현장 상태를 지어내지 마세요.
정보가 부족하면 필요한 추가 정보를 질문하세요. 작업 승인이나 안전을 확정하지 말고,
현장 안전관리자의 최종 확인이 필요하다는 점을 위험도가 높은 상황에서 분명히 알리세요."""


class AIConfigurationError(RuntimeError):
    pass


class AIService:
    def answer(self, question: str, context: str | None = None) -> str:
        if not settings.openai_api_key:
            raise AIConfigurationError("OPENAI_API_KEY가 설정되지 않았습니다.")

        user_input = question
        if context:
            user_input = f"현재 작업 정보:\n{context}\n\n사용자 질문:\n{question}"

        response = OpenAI(api_key=settings.openai_api_key).responses.create(
            model=settings.openai_model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=user_input,
        )
        answer = response.output_text.strip()
        if not answer:
            raise RuntimeError("모델이 빈 응답을 반환했습니다.")
        return answer
