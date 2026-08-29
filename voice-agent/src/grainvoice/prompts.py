"""Загрузка версионированных промптов из YAML.

Промпты живут в prompts/ отдельно от кода (п.28 ТЗ). Версия — часть имени файла
(`grain_buyer.v1.yaml`), и она сохраняется вместе с результатом анализа, чтобы
через полгода было понятно, каким промптом получены данные в карточке.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from grainvoice.config import PROMPTS_DIR


class PromptNotFoundError(FileNotFoundError):
    """Запрошенный промпт отсутствует в prompts/."""


@dataclass(frozen=True)
class Prompt:
    """Промпт, загруженный из YAML.

    Attributes:
        name: Имя семейства промптов, например ``grain_buyer``.
        version: Номер версии.
        data: Разобранное содержимое YAML.
    """

    name: str
    version: int
    data: dict[str, Any]

    @property
    def ref(self) -> str:
        """Ссылка вида ``grain_buyer.v1`` — её пишем в CallAnalysis."""
        return f"{self.name}.v{self.version}"

    def render(self, field: str, **values: Any) -> str:
        """Подставить значения в поле промпта.

        Пропущенные переменные заменяются на «неизвестно», а не роняют звонок:
        неполная карточка фермера — норма, агент уточнит недостающее в разговоре.

        Args:
            field: Ключ верхнего уровня в YAML (``system``, ``greeting``, ...).
            **values: Значения для подстановки.

        Returns:
            Текст с подставленными значениями.

        Raises:
            KeyError: Если такого поля в промпте нет.
        """
        if field not in self.data:
            raise KeyError(f"В промпте {self.ref} нет поля '{field}'")

        template = str(self.data[field])
        declared = self.data.get("variables") or []
        filled = {var: values.get(var) or "неизвестно" for var in declared}
        filled.update({k: v for k, v in values.items() if v is not None})

        return template.format_map(_Defaulting(filled))


class _Defaulting(dict):
    """Словарь, который не роняет format_map на незнакомом плейсхолдере."""

    def __missing__(self, key: str) -> str:
        return "неизвестно"


@lru_cache
def load_prompt(ref: str, category: str) -> Prompt:
    """Загрузить промпт по ссылке вида ``grain_buyer.v1``.

    Args:
        ref: Имя и версия через точку.
        category: Подпапка в prompts/ — ``conversation`` или ``analysis``.

    Returns:
        Загруженный промпт.

    Raises:
        PromptNotFoundError: Если файла нет.
        ValueError: Если ссылка не соответствует формату ``name.vN``.
    """
    name, _, version_part = ref.rpartition(".")
    if not name or not version_part.startswith("v") or not version_part[1:].isdigit():
        raise ValueError(f"Ожидался формат 'имя.vN', получено: {ref!r}")

    path: Path = PROMPTS_DIR / category / f"{ref}.yaml"
    if not path.is_file():
        raise PromptNotFoundError(f"Промпт не найден: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Prompt(name=name, version=int(version_part[1:]), data=data)
