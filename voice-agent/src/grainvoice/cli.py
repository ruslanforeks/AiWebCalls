"""Командная строка для работы со справочниками до появления веб-интерфейса.

    python -m grainvoice.cli import файл.xlsx
    python -m grainvoice.cli import файл.xlsx --export контакты.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from grainvoice.importer import ImportError_, ImportReport, LeadKind, import_excel

_KIND_LABELS = {
    LeadKind.GRAIN: "зерновые и масличные",
    LeadKind.UNKNOWN: "описание отсутствует",
    LeadKind.PROCESSOR: "переработка",
    LeadKind.TRADER: "закупщики и элеваторы",
    LeadKind.LIVESTOCK: "животноводство",
    LeadKind.OTHER_AGRO: "сады, овощи, виноград",
    LeadKind.NON_AGRO: "не сельское хозяйство",
}

_EXPORT_COLUMNS = [
    "Телефон", "Тип номера", "Компания", "Должность", "Контакт",
    "Регион", "Район", "Описание", "Категория", "Емейл", "Сайт",
]


def _print_report(report: ImportReport) -> None:
    """Показать итог импорта так, как он будет выглядеть в окне загрузки."""
    print(f"Импортировано:      {report.imported}")
    print(f"Дубликаты:          {report.duplicates}")
    print(f"Пропущено с ошибкой:{len(report.issues):>4}")
    print()
    print(f"ПОЙДУТ В ОБЗВОН:    {report.callable_count}")
    print()

    for kind, count in sorted(report.by_kind().items(), key=lambda item: -item[1]):
        mark = "звоним " if kind.worth_calling else "пропуск"
        print(f"  {mark}  {count:>5}  {_KIND_LABELS[kind]}")

    if report.issues:
        print(f"\nПроблемные строки ({len(report.issues)}):")
        for issue in report.issues[:10]:
            print(f"  строка {issue.row}: {issue.reason}")
        if len(report.issues) > 10:
            print(f"  ... и ещё {len(report.issues) - 10}")


def _export_csv(report: ImportReport, path: Path, only_callable: bool) -> int:
    """Выгрузить контакты в CSV.

    Args:
        report: Результат импорта.
        path: Куда писать.
        only_callable: Выгружать только тех, кому есть смысл звонить.

    Returns:
        Сколько строк записано.
    """
    records = [r for r in report.records if r.is_callable] if only_callable else report.records

    # utf-8-sig, иначе Excel на Windows покажет кириллицу кракозябрами.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(_EXPORT_COLUMNS)
        for record in records:
            phone = record.primary_phone
            writer.writerow(
                [
                    phone.e164 if phone else "",
                    phone.kind.value if phone else "",
                    record.company,
                    record.position or "",
                    record.contact_name or "",
                    record.region or "",
                    record.district or "",
                    record.description or "",
                    _KIND_LABELS[record.lead_kind],
                    record.email or "",
                    record.website or "",
                ]
            )

    return len(records)


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        prog="grainvoice",
        description="Работа со справочниками хозяйств",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="прочитать Excel и показать итог")
    imp.add_argument("file", type=Path, help="путь к .xlsx")
    imp.add_argument("--export", type=Path, metavar="CSV", help="выгрузить контакты в CSV")
    imp.add_argument(
        "--all",
        action="store_true",
        help="выгружать всех, а не только тех, кому есть смысл звонить",
    )

    args = parser.parse_args(argv)

    try:
        report = import_excel(args.file)
    except ImportError_ as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    _print_report(report)

    if args.export:
        written = _export_csv(report, args.export, only_callable=not args.all)
        print(f"\nВыгружено в {args.export}: {written} строк")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
