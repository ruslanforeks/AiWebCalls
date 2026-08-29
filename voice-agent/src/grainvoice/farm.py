"""Что известно о хозяйстве до звонка.

Агент звонит не в пустоту: в базе уже есть название, район и культуры.
Без этих данных он спрашивает то, что и так записано, и тратит время
занятого человека на подтверждение известного.

Второе применение — подсказка распознаванию. «Белоглинский» без подсказки
слышится как «минский» или «янтарский»: название района редкое, а вариантов
у распознавателя много. Зная, куда звоним, подсказываем одно нужное слово
вместо сорока четырёх районов края.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Слова, которые распознаватель уродует без подсказки: профессиональный
#: словарь зерновой торговли встречается в обучающих данных редко.
GRAIN_KEYTERMS = [
    "пшеница", "ячмень", "кукуруза", "подсолнечник", "рапс", "соя",
    "озимая", "яровая", "протеин", "клейковина", "натура", "влажность",
    "сорная примесь", "класс", "тонна", "гектар", "урожайность",
    "элеватор", "самовывоз", "предоплата", "КФХ",
]


@dataclass(frozen=True)
class FarmContext:
    """Карточка хозяйства, известная до звонка."""

    company: str = ""
    contact_name: str = ""
    region: str = ""
    district: str = ""
    #: Культуры из описания в базе: «пшеница, подсолнечник, соя».
    known_crops: list[str] = field(default_factory=list)

    @property
    def crops_phrase(self) -> str:
        """Культуры одной фразой для промпта, либо пусто."""
        return ", ".join(self.known_crops)

    def keyterms(self) -> list[str]:
        """Подсказки распознаванию для этого конкретного звонка.

        Порядок важен: сначала то, что относится к этому хозяйству,
        потом общий словарь.
        """
        terms: list[str] = []

        # Район и населённые пункты — самое ценное: их распознаватель
        # уродует чаще всего, а произносят их почти в каждом разговоре.
        for value in (self.district, self.region, self.company):
            cleaned = _clean(value)
            if cleaned and cleaned not in terms:
                terms.append(cleaned)

        for crop in self.known_crops:
            if crop and crop not in terms:
                terms.append(crop)

        for term in GRAIN_KEYTERMS:
            if term not in terms:
                terms.append(term)

        return terms

    def describe(self) -> str:
        """Строка для промпта: что мы знаем о хозяйстве.

        Пустая, если не знаем ничего — тогда агент выясняет всё сам.
        """
        parts = []
        if self.company:
            parts.append(f"Хозяйство: {self.company}.")
        if self.contact_name:
            parts.append(f"Собеседника зовут {self.contact_name}.")

        where = " ".join(x for x in (self.district, self.region) if x)
        if where:
            parts.append(f"Находится: {where}.")

        if self.known_crops:
            parts.append(f"По нашим данным выращивает: {self.crops_phrase}.")

        return " ".join(parts)


def _clean(value: str) -> str:
    """Убрать из названия района служебные хвосты.

    В справочнике район записан как «Белоглинский район», а произносят
    его обычно одним словом.
    """
    if not value:
        return ""

    for suffix in (" район", " р-н", " г.", " край"):
        value = value.replace(suffix, "")

    return value.strip()
