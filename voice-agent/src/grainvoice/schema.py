"""Схема структурированного результата анализа звонка.

Это контракт между голосовым агентом и CRM: ровно эти поля уезжают в карточку
фермера. Почти всё опционально осознанно — пустое поле честнее выдуманного
(п.29 ТЗ).
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class InterestLevel(str, Enum):
    """Насколько фермер заинтересован в продаже."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class CallOutcome(str, Enum):
    """Чем закончился звонок."""

    CONVERSATION = "conversation"
    """Разговор состоялся."""

    WRONG_NUMBER = "wrong_number"
    """Не тот человек или номер."""

    REFUSED = "refused"
    """Отказался разговаривать."""

    CALLBACK_REQUESTED = "callback_requested"
    """Попросил перезвонить в другое время."""

    NO_ANSWER = "no_answer"
    """Не дозвонились или сразу бросили трубку."""

    VOICEMAIL = "voicemail"
    """Ответил автоответчик или голосовое меню.

    Отделено от no_answer, потому что стоит денег: соединение состоялось,
    минуты связи тарифицируются, агент говорит с записью. По этому исходу
    видно, сколько кампания тратит впустую, и стоит ли учить агента
    распознавать автоответчик и класть трубку.
    """


class CallAnalysis(BaseModel):
    """Данные, извлечённые из транскрипта разговора.

    Поля со значением ``None`` означают «в разговоре не прозвучало».
    Модели прямо запрещено угадывать — см. промпт call_analysis.
    """

    outcome: CallOutcome = Field(description="Чем закончился звонок")

    has_grain: bool | None = Field(
        default=None,
        description="Есть ли у фермера зерно на продажу. None — не выяснено.",
    )
    crop: str | None = Field(
        default=None,
        description="Культура: пшеница, ячмень, кукуруза, подсолнечник и т.д.",
    )
    volume_tons: float | None = Field(
        default=None,
        ge=0,
        description="Объём в тоннах. Только если названо число.",
    )
    protein: float | None = Field(
        default=None,
        ge=0,
        le=30,
        description=(
            "Протеин (белок) в процентах. Главный показатель: по нему считается "
            "цена. Только если фермер назвал число."
        ),
    )
    quality: str | None = Field(
        default=None,
        description="Прочее о качестве, как назвал фермер: «3 класс», «клейковина 23».",
    )
    moisture: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Влажность в процентах, если названа.",
    )
    location: str | None = Field(
        default=None,
        description="Где находится зерно: район, хозяйство, элеватор.",
    )
    expected_price: float | None = Field(
        default=None,
        ge=0,
        description="Цена за тонну в рублях, которую хочет фермер.",
    )
    works_with_vat: bool | None = Field(
        default=None,
        description=(
            "Работает ли хозяйство с НДС. None — не выяснено. "
            "От этого зависит цена: разница десять процентов."
        ),
    )
    quoted_price: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Цена за тонну, которую агент назвал фермеру по прайсу. "
            "Нужна, чтобы менеджер видел, на чём остановился разговор. "
            "Записывается ровно та сумма, что прозвучала, без пересчёта НДС."
        ),
    )
    price_agreed: bool | None = Field(
        default=None,
        description=(
            "Согласился ли фермер с названной ценой. None — цена не звучала "
            "или реакция неясна."
        ),
    )
    ready_date: date | None = Field(
        default=None,
        description="С какой даты готов продавать.",
    )

    interest_level: InterestLevel = Field(description="Уровень интереса к сделке")

    needs_callback: bool = Field(
        default=False,
        description="Нужно ли перезвонить",
    )
    callback_date: date | None = Field(
        default=None,
        description="Когда перезвонить. Заполняется только при needs_callback.",
    )
    refusal_reason: str | None = Field(
        default=None,
        description="Причина отказа, если фермер отказался.",
    )

    summary: str = Field(
        description="Две-три фразы для менеджера, который не слушал разговор.",
    )
    recommended_action: str | None = Field(
        default=None,
        description="Что менеджеру сделать дальше.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Уверенность в извлечённых данных. Ниже 0.5 — показать предупреждение.",
    )


class AnalysisRecord(BaseModel):
    """Результат анализа вместе с метаданными о том, чем он получен.

    Хранить модель и версию промпта обязательно (п.27 ТЗ): промпты меняются,
    и через полгода нужно понимать, каким именно получены цифры в карточке.
    """

    analysis: CallAnalysis
    model: str = Field(description="ID модели, выполнившей анализ")
    prompt_ref: str = Field(description="Версия промпта, например call_analysis.v1")
    analyzed_at: str = Field(description="Время анализа в ISO 8601")
    duration_ms: int = Field(description="Сколько занял анализ")
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def is_low_confidence(self) -> bool:
        """Показывать ли менеджеру предупреждение о низкой уверенности."""
        return self.analysis.confidence < 0.5
