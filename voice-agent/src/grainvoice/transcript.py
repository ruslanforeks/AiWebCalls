"""Расшифровка разговора.

Собирается из истории LLMContext после завершения звонка. Отдельный тип,
а не список строк, потому что транскрипт переживёт смену голосового движка:
поменяется Pipecat — поменяется только `from_context`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

Speaker = Literal["agent", "farmer"]

# Роли из LLMContext, которые не являются репликами разговора:
# system/developer — служебные инструкции модели, в расшифровку не попадают.
_SKIPPED_ROLES = frozenset({"system", "developer", "tool"})


@dataclass
class Turn:
    """Одна реплика в разговоре."""

    speaker: Speaker
    text: str

    def __str__(self) -> str:
        label = "Закупщик" if self.speaker == "agent" else "Фермер"
        return f"{label}: {self.text}"


@dataclass
class Transcript:
    """Полная расшифровка одного звонка."""

    turns: list[Turn] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Прозвучала ли хоть одна реплика."""
        return not self.turns

    @property
    def farmer_spoke(self) -> bool:
        """Сказал ли фермер хоть что-то.

        Если нет — разговора не было (сброс, автоответчик), и звонок
        помечается как несостоявшийся без обращения к модели.
        """
        return any(t.speaker == "farmer" for t in self.turns)

    def add(self, speaker: Speaker, text: str) -> None:
        """Добавить реплику, игнорируя пустые."""
        cleaned = text.strip()
        if cleaned:
            self.turns.append(Turn(speaker=speaker, text=cleaned))

    def to_text(self) -> str:
        """Расшифровка в виде текста — то, что уходит в анализ."""
        return "\n".join(str(turn) for turn in self.turns)

    @classmethod
    def from_context(cls, messages: Iterable[Any]) -> "Transcript":
        """Собрать транскрипт из истории сообщений LLMContext.

        Args:
            messages: Содержимое ``LLMContext.messages``.

        Returns:
            Расшифровка, где ``assistant`` — агент, ``user`` — фермер.
        """
        transcript = cls()
        for message in messages:
            role = _get(message, "role")
            if role in _SKIPPED_ROLES:
                continue

            text = _extract_text(_get(message, "content"))
            if role == "assistant":
                transcript.add("agent", text)
            elif role == "user":
                transcript.add("farmer", text)

        return transcript


def _get(message: Any, key: str) -> Any:
    """Прочитать поле сообщения — оно может быть словарём или объектом."""
    if isinstance(message, dict):
        return message.get(key)
    return getattr(message, key, None)


def _extract_text(content: Any) -> str:
    """Достать текст из content, который бывает строкой или списком блоков."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif _get(block, "type") == "text":
                parts.append(str(_get(block, "text") or ""))
        return " ".join(p for p in parts if p)

    return ""
