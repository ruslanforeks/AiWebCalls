"""Извлечение структурированных данных из текста разными LLM-провайдерами.

Провайдеры отличаются тем, как гарантируют форму ответа:

- Anthropic умеет строгую схему на своей стороне — ответ приходит уже валидным.
- DeepSeek (как и прочие OpenAI-совместимые) даёт только «верни JSON»,
  а соответствие схеме приходится проверять у себя и просить исправить.

Эта разница спрятана здесь, чтобы analysis.py не знал, кто за ней стоит (п.27 ТЗ).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

TModel = TypeVar("TModel", bound=BaseModel)


class ExtractionError(RuntimeError):
    """Не удалось получить структурированный ответ от модели."""


@dataclass(frozen=True)
class Extraction:
    """Результат извлечения вместе с расходом токенов."""

    data: BaseModel
    input_tokens: int
    output_tokens: int


class Extractor(Protocol):
    """Что должен уметь провайдер, чтобы его можно было подставить."""

    model: str

    async def extract(
        self, system: str, user: str, schema: type[TModel]
    ) -> Extraction:
        """Получить из текста объект указанной схемы."""
        ...


class DeepSeekExtractor:
    """Извлечение через DeepSeek (OpenAI-совместимый API).

    DeepSeek умеет только режим «ответь валидным JSON», без проверки по схеме.
    Поэтому схему кладём в промпт, а результат проверяем сами и при ошибке
    даём модели один шанс исправиться — на практике этого хватает.
    """

    #: Сколько раз просить модель переделать ответ, не прошедший валидацию.
    MAX_RETRIES = 1

    #: Потолок на ответ. Взят с запасом: модели с рассуждением тратят бюджет
    #: на размышления перед JSON, и при тесном лимите ответ обрывается
    #: на середине строки — валидация падает, а причина выглядит как
    #: «модель не умеет в схему», хотя дело в потолке.
    MAX_TOKENS = 8192

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        import openai

        self.model = model
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def extract(self, system: str, user: str, schema: type[TModel]) -> Extraction:
        """Получить объект схемы, при необходимости попросив исправить JSON.

        Raises:
            ExtractionError: Если после повтора ответ так и не прошёл валидацию.
        """
        import openai

        messages = [
            {"role": "system", "content": f"{system}\n\n{_schema_instruction(schema)}"},
            {"role": "user", "content": user},
        ]

        input_tokens = output_tokens = 0
        last_error = ""

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=self.MAX_TOKENS,
                    # Извлечение фактов, а не сочинение: разброс здесь только вредит.
                    temperature=0.0,
                )
            except openai.APIStatusError as exc:
                raise ExtractionError(
                    f"DeepSeek вернул ошибку {exc.status_code}: {exc.message}"
                ) from exc
            except openai.APIConnectionError as exc:
                raise ExtractionError("Не удалось связаться с DeepSeek") from exc

            if response.usage:
                input_tokens += response.usage.prompt_tokens
                output_tokens += response.usage.completion_tokens

            choice = response.choices[0]
            if choice.finish_reason == "length":
                raise ExtractionError(
                    f"Ответ модели оборван на лимите {self.MAX_TOKENS} токенов. "
                    "Разговор слишком длинный либо модель многословна — "
                    "поднимите MAX_TOKENS или возьмите модель без рассуждений."
                )

            raw = (choice.message.content or "").strip()
            try:
                return Extraction(
                    data=schema.model_validate_json(raw),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt == self.MAX_RETRIES:
                    break
                logger.warning("DeepSeek вернул ответ не по схеме, прошу исправить")
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": (
                                "Ответ не соответствует схеме:\n"
                                f"{last_error}\n\n"
                                "Верни исправленный JSON. Только JSON, без пояснений."
                            ),
                        },
                    ]
                )

        raise ExtractionError(f"Ответ не соответствует схеме: {last_error}")


class AnthropicExtractor:
    """Извлечение через Claude со строгой схемой на стороне API."""

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self.model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def extract(self, system: str, user: str, schema: type[TModel]) -> Extraction:
        """Получить объект схемы. Форму гарантирует сам API.

        Raises:
            ExtractionError: Если API вернул ошибку или пустой результат.
        """
        import anthropic

        try:
            response = await self._client.messages.parse(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.APIStatusError as exc:
            raise ExtractionError(
                f"Claude вернул ошибку {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("Не удалось связаться с Claude") from exc

        if response.parsed_output is None:
            raise ExtractionError("Модель не вернула структурированный результат")

        return Extraction(
            data=response.parsed_output,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _schema_instruction(schema: type[BaseModel]) -> str:
    """Описание схемы для провайдеров, которые не умеют её проверять сами."""
    return (
        "Ответь строго одним JSON-объектом по этой схеме.\n"
        "Без markdown, без пояснений, без обёртки в ```.\n"
        "Поля, значение которых не прозвучало, оставляй null.\n\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}"
    )


def build_extractor(settings: "Settings") -> Extractor:  # noqa: F821
    """Создать извлекатель по настройкам.

    Args:
        settings: Конфигурация с выбранным провайдером.

    Returns:
        Готовый извлекатель.
    """
    from grainvoice.config import LLMProvider

    settings.require_llm()

    if settings.llm_provider is LLMProvider.DEEPSEEK:
        return DeepSeekExtractor(
            api_key=settings.deepseek_api_key,
            model=settings.analysis_model,
            base_url=settings.deepseek_base_url,
        )

    return AnthropicExtractor(
        api_key=settings.anthropic_api_key,
        model=settings.analysis_model,
    )
