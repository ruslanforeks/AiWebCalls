"""Импорт справочника хозяйств из Excel.

Написано под реальную выгрузку по Краснодарскому краю, но колонки ищутся по
названиям, а не по позициям, — выгрузки по другим регионам обычно отличаются
порядком столбцов.

Главное, что делает импорт помимо чтения: отсеивает тех, кому звонить незачем.
В справочнике вперемешку лежат зерновые хозяйства, хлебозаводы, рыбзаводы
и автоперевозчики. Обзвон каждого контакта стоит денег, поэтому классификация
по полю «Описание» — не украшение, а способ не потратить бюджет впустую.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from grainvoice.phones import Phone, best_phone, parse_phones


class LeadKind(str, Enum):
    """К какой категории отнесено хозяйство по описанию деятельности."""

    GRAIN = "grain"
    """Растениеводство: зерновые, масличные. Основная цель обзвона."""

    PROCESSOR = "processor"
    """Переработка: хлебозаводы, мельницы, крупозаводы. Зерно покупают, а не продают."""

    TRADER = "trader"
    """Закупщики, элеваторы, перевалка. Это конкуренты и посредники.

    Звонок им хуже, чем бесполезен: вы спрашиваете у конкурента, не продаст ли
    он вам зерно, и заодно показываете, что ищете объёмы.
    """

    LIVESTOCK = "livestock"
    """Животноводство. Зерно скорее покупают на корм."""

    OTHER_AGRO = "other_agro"
    """Сады, овощи, виноград. Не наш профиль."""

    NON_AGRO = "non_agro"
    """Не сельское хозяйство вообще."""

    UNKNOWN = "unknown"
    """Описание пустое или непонятное — звонить, чтобы выяснить."""

    @property
    def worth_calling(self) -> bool:
        """Стоит ли тратить звонок на этот контакт."""
        return self in (LeadKind.GRAIN, LeadKind.UNKNOWN)


# Классификация по ключевым словам. Порядок проверки важен: переработку
# и закупщиков определяем раньше зерна, иначе «производство рисовой крупы»
# и «закупка масличных» попадут в цель обзвона из-за слов «рис» и «масличных».
_TRADER = (
    "закупка", "закупк", "приемка", "приёмка", "перевалка", "хранение зерн",
    "подработка", "оптовая торговля зерн", "трейд", "хлебопродукт",
    "экспорт зерн", "реализация зерн", "элеватор",
)
_PROCESSOR = (
    "хлебозавод", "хлебокомбинат", "кондитер", "пекарн", "мукомол", "комбикорм",
    "крупян", "крупы", "маслозавод", "масложир", "элеватор", "мельниц",
    "переработк", "консерв", "сахарн", "спиртзавод", "пивовар",
)
_GRAIN = (
    "пшениц", "ячмен", "кукуруз", "подсолнечник", "рапс", "соя", "сои", "овёс",
    "овес", "рожь", "гречих", "зерно", "зернов", "масличн", "сорго", "горох",
    "нут", "люцерн", "растениевод", "полевод", "тритикале", "рис",
)
_LIVESTOCK = (
    "молок", "мясо", "свинов", "птицев", "крс", "скотовод", "животновод",
    "рыб", "яйц", "пчел", "мёд", "овцевод", "коневод", "инкубат",
)
_OTHER_AGRO = (
    "яблок", "плодовод", "виноград", "овощ", "теплиц", "саженц", "цвет",
    "ягод", "орех", "питомник", "персик", "черешн", "садовод", "клубник",
    "земляник", "бахч", "картофел",
)

#: Должности, с которых начинается поле «Имя»: «Глава:», «Ген. дир.:».
_POSITION_RE = re.compile(r"^\s*([^:]{1,40}?)\s*:\s*(.+)$")

#: Как называются нужные колонки. Ищем по вхождению, регистр не важен.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "company": ("название", "компания", "организация", "хозяйство", "наименование"),
    "description": ("описание", "деятельность", "культуры", "специализация"),
    "region": ("регион", "область", "край"),
    "district": ("город", "район", "населённый", "населенный"),
    "contact": ("имя", "фио", "руководитель", "контактное лицо"),
    "email": ("емейл", "email", "e-mail", "почта"),
    "website": ("сайт", "website", "url"),
    "phones": ("телефон", "тел.", "телефоны", "контакты"),
    "address": ("адрес",),
}


@dataclass
class FarmerRecord:
    """Одно хозяйство, готовое к записи в CRM."""

    company: str
    contact_name: str | None = None
    position: str | None = None
    region: str | None = None
    district: str | None = None
    description: str | None = None
    email: str | None = None
    website: str | None = None
    address: str | None = None

    phones: list[Phone] = field(default_factory=list)
    lead_kind: LeadKind = LeadKind.UNKNOWN

    @property
    def primary_phone(self) -> Phone | None:
        """Номер, по которому будет звонить агент."""
        return best_phone(self.phones)

    @property
    def is_callable(self) -> bool:
        """Есть ли смысл ставить контакт в очередь обзвона."""
        return self.primary_phone is not None and self.lead_kind.worth_calling


@dataclass
class ImportIssue:
    """Строка, которую не удалось импортировать."""

    row: int
    reason: str


@dataclass
class ImportReport:
    """Итог импорта — то, что показывается пользователю после загрузки файла."""

    records: list[FarmerRecord] = field(default_factory=list)
    duplicates: int = 0
    issues: list[ImportIssue] = field(default_factory=list)

    @property
    def imported(self) -> int:
        return len(self.records)

    @property
    def callable_count(self) -> int:
        """Сколько контактов реально пойдут в обзвон."""
        return sum(1 for r in self.records if r.is_callable)

    def by_kind(self) -> dict[LeadKind, int]:
        """Распределение по категориям — для показа в окне импорта."""
        counts: dict[LeadKind, int] = {}
        for record in self.records:
            counts[record.lead_kind] = counts.get(record.lead_kind, 0) + 1
        return counts


class ImportError_(ValueError):
    """Файл не удалось прочитать."""


def classify(description: str | None) -> LeadKind:
    """Определить категорию хозяйства по описанию деятельности.

    Args:
        description: Содержимое колонки «Описание».

    Returns:
        Категория; ``UNKNOWN``, если описание пустое или не распознано.
    """
    if not description:
        return LeadKind.UNKNOWN

    text = description.lower()
    if not text.strip() or text.strip() == "none":
        return LeadKind.UNKNOWN

    # Закупщики и переработчики проверяются раньше культур: «закупка масличных»
    # и «производство рисовой крупы» содержат нужные слова, но это покупатели
    # зерна, а не поставщики.
    if any(w in text for w in _TRADER):
        return LeadKind.TRADER
    if any(w in text for w in _PROCESSOR):
        return LeadKind.PROCESSOR
    if any(w in text for w in _GRAIN):
        return LeadKind.GRAIN
    if any(w in text for w in _LIVESTOCK):
        return LeadKind.LIVESTOCK
    if any(w in text for w in _OTHER_AGRO):
        return LeadKind.OTHER_AGRO

    return LeadKind.NON_AGRO


def split_position(value: str | None) -> tuple[str | None, str | None]:
    """Разделить «Глава: Иванов Иван Иванович» на должность и ФИО.

    Args:
        value: Содержимое колонки «Имя».

    Returns:
        Пара «должность, ФИО». Любая часть может быть None.
    """
    if not value:
        return None, None

    text = str(value).strip()
    if not text or text.lower() == "none":
        return None, None

    match = _POSITION_RE.match(text)
    if match:
        return match.group(1).strip(" ."), match.group(2).strip()

    return None, text


def import_excel(path: str | Path) -> ImportReport:
    """Прочитать файл справочника и превратить в записи для CRM.

    Args:
        path: Путь к .xlsx.

    Returns:
        Отчёт с записями, дублями и списком проблемных строк.

    Raises:
        ImportError_: Если файл нечитаем или в нём не найдены нужные колонки.
    """
    import openpyxl

    path = Path(path)
    if not path.is_file():
        raise ImportError_(f"Файл не найден: {path}")

    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl бросает разное на битых файлах
        raise ImportError_(f"Не удалось прочитать файл: {exc}") from exc

    sheet = workbook[workbook.sheetnames[0]]
    rows = sheet.iter_rows(values_only=True)

    try:
        header = next(rows)
    except StopIteration:
        raise ImportError_("Файл пустой") from None

    columns = _map_columns(header)
    if "phones" not in columns:
        raise ImportError_(
            "Не найдена колонка с телефонами. "
            f"Заголовки файла: {', '.join(str(h) for h in header if h)}"
        )

    return _build_report(rows, columns)


def _build_report(rows: Iterator[tuple[Any, ...]], columns: dict[str, int]) -> ImportReport:
    """Собрать отчёт, отсеивая дубли по номеру телефона."""
    report = ImportReport()
    seen_phones: set[str] = set()

    for line, row in enumerate(rows, start=2):
        cell = lambda key: _cell(row, columns.get(key))  # noqa: E731

        company = cell("company")
        phones = parse_phones(cell("phones"))

        if not company and not phones:
            continue  # пустая строка в конце файла

        if not company:
            report.issues.append(ImportIssue(line, "нет названия хозяйства"))
            continue

        if not phones:
            report.issues.append(ImportIssue(line, f"нет телефона: {company[:50]}"))
            continue

        primary = best_phone(phones)
        if primary and primary.e164 in seen_phones:
            report.duplicates += 1
            continue
        if primary:
            seen_phones.add(primary.e164)

        position, name = split_position(cell("contact"))
        description = cell("description")

        report.records.append(
            FarmerRecord(
                company=company,
                contact_name=name,
                position=position,
                region=cell("region"),
                district=cell("district"),
                description=description,
                email=cell("email"),
                website=cell("website"),
                address=cell("address"),
                phones=phones,
                lead_kind=classify(description),
            )
        )

    return report


def _map_columns(header: tuple[Any, ...]) -> dict[str, int]:
    """Сопоставить наши поля с колонками файла по названиям заголовков."""
    mapping: dict[str, int] = {}
    for index, title in enumerate(header):
        if not title:
            continue
        normalized = str(title).strip().lower()
        for field_name, aliases in _COLUMN_ALIASES.items():
            if field_name in mapping:
                continue
            if any(alias in normalized for alias in aliases):
                mapping[field_name] = index
                break
    return mapping


def _cell(row: tuple[Any, ...], index: int | None) -> str | None:
    """Достать значение ячейки, приведя пустые варианты к None."""
    if index is None or index >= len(row):
        return None
    value = row[index]
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() != "none" else None
