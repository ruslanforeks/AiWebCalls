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


#: Фразы, которые агент произносит почти в каждом звонке.
#: Приветствие сюда не входит — оно собирается из промпта и подставляется
#: отдельно, вместе с именем агента и названием компании.
_COMMON_PHRASES = (
    "Понял.",
    "Хорошо.",
    "Ага, понял.",
    "Вы с НДС работаете или без?",
    "Протеин какой?",
    "А протеин какой на эту партию?",
    "Сколько тонн примерно?",
    "Хорошо, передам руководителю, он свяжется.",
    "Понял. Согласую и перезвоню. Сколько бы вас устроило?",
    "Записал. Руководитель наберёт. Всего доброго.",
    "Это уже с руководителем, он наберёт и всё обговорит.",
    "Протеин скажете — назову цену.",
)


def _warm_cache() -> int:
    """Заранее озвучить приветствие и типовые фразы.

    Без прогрева первый звонок платит за синтез приветствия и ждёт его
    полсекунды. Прогрев переносит эту плату на один раз.
    """
    import asyncio

    from grainvoice.audio import native_output_rate, resolve_device
    from grainvoice.bot import build_conversation_prompt
    from grainvoice.certs import ensure_ca_bundle
    from grainvoice.config import get_settings
    from grainvoice.prices import PRICE_TIERS
    from grainvoice.tts_cache import warm_phrases

    ensure_ca_bundle()
    settings = get_settings()
    _, greeting = build_conversation_prompt(settings)

    phrases = [" ".join(greeting.split()), *_COMMON_PHRASES]

    # Фразы с ценой: их всего десять — пять ступеней протеина на два
    # варианта налогообложения. Модель формулирует их по-разному, поэтому
    # попадание не гарантировано, но самые частые формы стоит заготовить.
    for tier, price in PRICE_TIERS:
        for tax in ("без НДС", "с НДС"):
            phrases.append(f"{price} {tax} за тонну.")

    # Частота входит в ключ кэша, а зависит она от устройства вывода:
    # встроенный выход даёт 22050, AirPods — 24000. Переключение наушников
    # обнулило бы весь кэш, поэтому заготавливаем на всех рабочих частотах.
    device = resolve_device(settings.audio_output_device, want_input=False)
    current = settings.audio_out_sample_rate or native_output_rate(device) or 24000
    rates = sorted({current, 22050, 24000})

    total_written = total_skipped = 0
    for rate in rates:
        print(f"Озвучиваю {len(phrases)} фраз, {rate} Гц...")
        written, skipped = asyncio.run(warm_phrases(settings, phrases, sample_rate=rate))
        total_written += written
        total_skipped += skipped

    print(f"\n  записано: {total_written}")
    print(f"  уже были: {total_skipped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(
        prog="grainvoice",
        description="Работа со справочниками хозяйств",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "warm-cache",
        help="заранее озвучить приветствие и типовые фразы",
    )

    imp = sub.add_parser("import", help="прочитать Excel и показать итог")
    imp.add_argument("file", type=Path, help="путь к .xlsx")
    imp.add_argument("--export", type=Path, metavar="CSV", help="выгрузить контакты в CSV")
    imp.add_argument(
        "--all",
        action="store_true",
        help="выгружать всех, а не только тех, кому есть смысл звонить",
    )

    args = parser.parse_args(argv)

    if args.command == "warm-cache":
        return _warm_cache()

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
