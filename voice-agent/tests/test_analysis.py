"""Тесты анализа звонка.

LLM здесь подменён: проверяется наша обвязка — сборка запроса, метаданные
и обработка ошибок, а не качество извлечения (для него нужны живые звонки).
"""

from datetime import date
from typing import Any

import pytest

from grainvoice.analysis import AnalysisError, CallAnalyzer
from grainvoice.config import Settings
from grainvoice.extractors import Extraction, ExtractionError
from grainvoice.schema import CallAnalysis, CallOutcome, InterestLevel
from grainvoice.transcript import Transcript


class _FakeExtractor:
    """Подменяет провайдера и запоминает, с чем его вызвали."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.model = "fake-model"
        self._result = result
        self._error = error
        self.call: dict[str, Any] = {}

    async def extract(self, system: str, user: str, schema: type) -> Extraction:
        self.call = {"system": system, "user": user, "schema": schema}
        if self._error:
            raise self._error
        return Extraction(data=self._result, input_tokens=1200, output_tokens=180)


def _settings(**overrides) -> Settings:
    # Промпт задаётся явно: иначе тест зависит от .env разработчика
    # и падает при каждой смене версии сценария.
    defaults = {"DEEPSEEK_API_KEY": "test-key", "ANALYSIS_PROMPT": "call_analysis.v1"}
    return Settings(**{**defaults, **overrides})


def _transcript() -> Transcript:
    transcript = Transcript()
    transcript.add("agent", "Здравствуйте! Есть зерно на продажу?")
    transcript.add("farmer", "Есть, пшеница, тонн восемьсот, третий класс")
    return transcript


def _analysis(**overrides: Any) -> CallAnalysis:
    defaults: dict[str, Any] = {
        "outcome": CallOutcome.CONVERSATION,
        "has_grain": True,
        "crop": "Пшеница",
        "volume_tons": 800,
        "quality": "3 класс",
        "interest_level": InterestLevel.HIGH,
        "summary": "Есть 800 т пшеницы 3 класса.",
        "confidence": 0.9,
    }
    return CallAnalysis(**{**defaults, **overrides})


async def test_records_model_and_prompt_version() -> None:
    """Модель и версия промпта сохраняются — иначе данные в CRM не отследить."""
    extractor = _FakeExtractor(result=_analysis())

    settings = _settings(ANALYSIS_PROMPT="call_analysis.v2")
    record = await CallAnalyzer(settings, extractor=extractor).analyze(_transcript())

    assert record.model == "fake-model"
    # Версия берётся из настроек, а не из умолчания: через полгода будет
    # видно, каким промптом получены цифры в конкретной карточке.
    assert record.prompt_ref == "call_analysis.v2"
    assert record.input_tokens == 1200
    assert record.analyzed_at.startswith("20")


async def test_sends_transcript_and_date() -> None:
    """В запрос уходит расшифровка и дата отсчёта для «через неделю»."""
    extractor = _FakeExtractor(result=_analysis())

    await CallAnalyzer(_settings(), extractor=extractor).analyze(
        _transcript(), today=date(2026, 8, 28)
    )

    assert "восемьсот" in extractor.call["user"]
    assert "28.08.2026" in extractor.call["system"]
    assert extractor.call["schema"] is CallAnalysis


async def test_low_confidence_is_flagged() -> None:
    """Неуверенный разбор помечается, чтобы менеджер проверил вручную."""
    extractor = _FakeExtractor(result=_analysis(confidence=0.35))

    record = await CallAnalyzer(_settings(), extractor=extractor).analyze(_transcript())

    assert record.is_low_confidence is True


async def test_confident_result_is_not_flagged() -> None:
    extractor = _FakeExtractor(result=_analysis(confidence=0.9))

    record = await CallAnalyzer(_settings(), extractor=extractor).analyze(_transcript())

    assert record.is_low_confidence is False


async def test_empty_transcript_rejected() -> None:
    """Пустой транскрипт не отправляем в модель — платить не за что."""
    extractor = _FakeExtractor(result=_analysis())

    with pytest.raises(AnalysisError, match="пустой"):
        await CallAnalyzer(_settings(), extractor=extractor).analyze(Transcript())


async def test_provider_error_becomes_analysis_error() -> None:
    """Ошибка провайдера не должна всплывать наружу как чужое исключение."""
    extractor = _FakeExtractor(error=ExtractionError("DeepSeek вернул ошибку 429"))

    with pytest.raises(AnalysisError, match="429"):
        await CallAnalyzer(_settings(), extractor=extractor).analyze(_transcript())


def test_unknown_fields_stay_none() -> None:
    """Незаполненные поля остаются пустыми — п.29 ТЗ, не выдумывать значения."""
    result = _analysis(expected_price=None, moisture=None)

    assert result.expected_price is None
    assert result.moisture is None
