"""Нормализация российских телефонных номеров из справочников.

Вынесено отдельно, потому что в реальных базах номера записаны как попало,
и это единственное место, где ошибка стоит дорого: неверно разобранный номер —
это либо несостоявшийся звонок, либо звонок постороннему человеку.

Форматы, которые встречаются в базе по Краснодарскому краю:

    (86150) 4-45-70                 стационарный с кодом в скобках
    8-918-432-0035                  мобильный
    (86150) 4-50-71, 4-47-07        второй номер наследует код первого
    8-800-505-0415                  федеральный — на такой звонить бессмысленно
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: Длина российского номера без кода страны.
_NATIONAL_LENGTH = 10

#: Префиксы, на которые нет смысла звонить фермеру:
#: 800 — федеральный номер организации, 3/4 — служебные.
_NON_DIALABLE_PREFIXES = ("800",)


class PhoneKind(str, Enum):
    """Тип номера — определяет, стоит ли по нему звонить."""

    MOBILE = "mobile"
    """Мобильный. Приоритет для обзвона: отвечает сам человек."""

    LANDLINE = "landline"
    """Стационарный. Часто приёмная или никто не берёт."""

    TOLL_FREE = "toll_free"
    """Федеральный 8-800. Ведёт в колл-центр, для нас бесполезен."""


@dataclass(frozen=True)
class Phone:
    """Разобранный номер."""

    e164: str
    """Номер в формате +7XXXXXXXXXX."""

    kind: PhoneKind
    raw: str
    """Как было записано в исходной таблице — на случай разбора ошибок."""

    @property
    def is_dialable(self) -> bool:
        """Стоит ли вообще звонить по этому номеру."""
        return self.kind is not PhoneKind.TOLL_FREE


def parse_phones(cell: str | None) -> list[Phone]:
    """Разобрать ячейку с телефонами в список номеров.

    Номера без кода города наследуют код у предыдущего номера в той же ячейке:
    в справочниках «(86150) 4-50-71, 4-47-07» означает два номера одного города.

    Args:
        cell: Содержимое ячейки «Телефоны».

    Returns:
        Разобранные номера без дублей, в порядке появления.
    """
    if not cell or str(cell).strip().lower() in ("", "none"):
        return []

    text = str(cell)
    result: list[Phone] = []
    seen: set[str] = set()
    area_code = ""

    for chunk in re.split(r"[,;]| и ", text):
        chunk = chunk.strip()
        if not chunk:
            continue

        # Код города в скобках задаёт контекст для следующих номеров ячейки.
        bracketed = re.match(r"^\(?(\d{3,5})\)", chunk)
        if bracketed:
            area_code = bracketed.group(1)

        phone = _parse_one(chunk, area_code)
        if phone and phone.e164 not in seen:
            seen.add(phone.e164)
            result.append(phone)

    return result


def _parse_one(chunk: str, area_code: str) -> Phone | None:
    """Разобрать один фрагмент. Возвращает None, если это не номер."""
    digits = re.sub(r"\D", "", chunk)
    if not digits:
        return None

    national = _to_national(digits, area_code)
    if national is None:
        return None

    return Phone(e164=f"+7{national}", kind=_classify(national), raw=chunk.strip())


def _to_national(digits: str, area_code: str) -> str | None:
    """Привести цифры к десятизначному национальному номеру.

    Returns:
        Десять цифр, либо None если номер не восстанавливается.
    """
    # 8XXXXXXXXXX или 7XXXXXXXXXX — ведущая цифра является кодом страны.
    if len(digits) == _NATIONAL_LENGTH + 1 and digits[0] in "87":
        return digits[1:]

    if len(digits) == _NATIONAL_LENGTH:
        return digits

    # Короткий местный номер — достраиваем кодом города из той же ячейки.
    if area_code and not digits.startswith(area_code):
        combined = area_code + digits
        if len(combined) == _NATIONAL_LENGTH:
            return combined

    return None


def _classify(national: str) -> PhoneKind:
    """Определить тип по десятизначному номеру."""
    if national.startswith(_NON_DIALABLE_PREFIXES):
        return PhoneKind.TOLL_FREE
    if national.startswith("9"):
        return PhoneKind.MOBILE
    return PhoneKind.LANDLINE


def best_phone(phones: list[Phone]) -> Phone | None:
    """Выбрать номер для автообзвона.

    Мобильный предпочтительнее стационарного: на него отвечает сам
    руководитель хозяйства, а не приёмная.

    Args:
        phones: Разобранные номера одного контакта.

    Returns:
        Лучший номер или None, если звонить некуда.
    """
    dialable = [p for p in phones if p.is_dialable]
    if not dialable:
        return None

    return next(
        (p for p in dialable if p.kind is PhoneKind.MOBILE),
        dialable[0],
    )
