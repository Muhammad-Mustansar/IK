from app.chat_service import ChatService
from app.llm_service import LLMService
from app.vector_search import VectorSearchService


def test_extract_budget() -> None:
    service = ChatService(LLMService(), VectorSearchService(LLMService()))
    budget = service._extract_budget("Show me options under $100")
    assert budget is not None
    assert str(budget) == "100"


def test_extract_interests() -> None:
    service = ChatService(LLMService(), VectorSearchService(LLMService()))
    interests = service._extract_interests("I need running shoes for marathon training")
    assert interests
    assert "running shoes" in interests[0].lower()


def test_extract_preferences() -> None:
    service = ChatService(LLMService(), VectorSearchService(LLMService()))
    preferences = service._extract_preferences("I prefer Nike brand and like black color")
    assert preferences.get("brand", "").lower() == "nike"
    assert preferences.get("color", "").lower() == "black"
