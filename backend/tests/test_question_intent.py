from app.schemas.chat import ChatRequest
from app.services.question_intent import classify_question_intent


def _intent(question: str) -> str:
    return classify_question_intent(ChatRequest(question=question)).intent


def test_document_summary_is_document_qa() -> None:
    assert _intent("이 PDF를 요약해줘.") == "document_qa"


def test_document_question_about_installation_is_document_qa() -> None:
    assert _intent("이 PDF에 라이트커튼 설치 방법이 있어?") == "document_qa"


def test_document_page_location_is_document_qa() -> None:
    assert _intent("이 문서 몇 페이지에 나와 있어?") == "document_qa"


def test_component_definition_is_component_info() -> None:
    assert _intent("라이트커튼이 무슨 장비인지 알려줘.") == "component_info"


def test_short_component_definition_is_component_info() -> None:
    assert _intent("라이트커튼이 뭐야?") == "component_info"


def test_component_usage_is_component_info() -> None:
    assert _intent("라이트커튼은 어디에 사용해?") == "component_info"


def test_component_what_does_it_do_phrase_is_component_info() -> None:
    assert _intent("비상정지 스위치는 뭐 하는 부품이야?") == "component_info"


def test_installation_request_is_maintenance_guide() -> None:
    assert _intent("라이트커튼 설치 방법을 알려줘.") == "maintenance_guide"


def test_cleaning_confirmation_is_maintenance_guide() -> None:
    assert _intent("컨베이어를 청소할 때 무엇을 확인해야 해?") == "maintenance_guide"


def test_press_cleaning_request_is_maintenance_guide() -> None:
    assert _intent("프레스 내부를 청소하려고 해.") == "maintenance_guide"


def test_ambiguous_component_question_requests_clarification() -> None:
    decision = classify_question_intent(
        ChatRequest(question="라이트커튼 관련해서 알려줘.")
    )

    assert decision.intent == "clarification_required"
    assert decision.clarification_question


def test_pronoun_only_how_to_request_requires_clarification() -> None:
    assert _intent("이거 어떻게 해?") == "clarification_required"
