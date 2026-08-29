"""Анализ транскрипта звонка: свободная речь → структурированные данные для CRM.

Отделён от голосового конвейера намеренно (п.27 ТЗ): распознавание, диалог
и анализ — три независимых компонента. Здесь задержка не критична, а цена
ошибки высока, поэтому модель берём умнее той, что вела разговор.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone

from loguru import logger

from grainvoice.config import Settings, get_settings
from grainvoice.extractors import ExtractionError, Extractor, build_extractor
from grainvoice.prompts import load_prompt
from grainvoice.schema import AnalysisRecord, CallAnalysis
from grainvoice.transcript import Transcript


class AnalysisError(RuntimeError):
    """Анализ не удался. Звонок при этом не теряется — транскрипт сохранён."""


class CallAnalyzer:
    """Извлекает структурированные данные из расшифровки разговора."""

    def __init__(
        self,
        settings: Settings | None = None,
        extractor: Extractor | None = None,
    ) -> None:
        """
        Args:
            settings: Настройки; по умолчанию читаются из окружения.
            extractor: Готовый извлекатель — точка подмены в тестах.
        """
        self._settings = settings or get_settings()
        self._extractor = extractor or build_extractor(self._settings)

    async def analyze(self, transcript: Transcript, today: date | None = None) -> AnalysisRecord:
        """Разобрать транскрипт в структуру для карточки фермера.

        Args:
            transcript: Расшифровка разговора.
            today: Дата отсчёта для относительных сроков («через неделю»).
                По умолчанию — сегодня.

        Returns:
            Результат анализа с метаданными о модели и версии промпта.

        Raises:
            AnalysisError: Если транскрипт пуст или модель не дала результат.
        """
        if transcript.is_empty:
            raise AnalysisError("Транскрипт пустой — анализировать нечего")

        prompt = load_prompt(self._settings.analysis_prompt, category="analysis")
        system = prompt.render("system", today=(today or date.today()).strftime("%d.%m.%Y"))
        user = prompt.render("user_template", transcript=transcript.to_text())

        started = time.monotonic()
        try:
            extraction = await self._extractor.extract(system, user, CallAnalysis)
        except ExtractionError as exc:
            raise AnalysisError(str(exc)) from exc

        result = extraction.data
        assert isinstance(result, CallAnalysis)

        record = AnalysisRecord(
            analysis=result,
            model=self._extractor.model,
            prompt_ref=prompt.ref,
            analyzed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=int((time.monotonic() - started) * 1000),
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
        )

        if record.is_low_confidence:
            logger.warning(
                "Низкая уверенность анализа ({:.2f}) — карточку стоит проверить вручную",
                result.confidence,
            )

        return record
