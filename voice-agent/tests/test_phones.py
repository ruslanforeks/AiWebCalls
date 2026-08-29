"""Тесты разбора телефонов.

Примеры взяты из реальной выгрузки по Краснодарскому краю.
"""

from grainvoice.phones import PhoneKind, best_phone, parse_phones


def test_mobile_number() -> None:
    phones = parse_phones("8-918-432-0035")

    assert len(phones) == 1
    assert phones[0].e164 == "+79184320035"
    assert phones[0].kind is PhoneKind.MOBILE


def test_landline_with_area_code_in_brackets() -> None:
    phones = parse_phones("(86150) 4-45-70")

    assert phones[0].e164 == "+78615044570"
    assert phones[0].kind is PhoneKind.LANDLINE


def test_second_number_inherits_area_code() -> None:
    """«(86150) 4-50-71, 4-47-07» — два номера одного города.

    Без наследования кода второй номер потерялся бы.
    """
    phones = parse_phones("(86150) 4-50-71, 4-47-07")

    assert [p.e164 for p in phones] == ["+78615045071", "+78615044707"]


def test_toll_free_marked_not_dialable() -> None:
    """8-800 ведёт в колл-центр — фермера там не будет."""
    phones = parse_phones("8-800-505-0415")

    assert phones[0].kind is PhoneKind.TOLL_FREE
    assert phones[0].is_dialable is False


def test_mixed_cell_from_real_data() -> None:
    phones = parse_phones("(86150) 4-50-71, 4-47-07, 8-800-505-0415")

    assert len(phones) == 3
    assert best_phone(phones).e164 == "+78615045071"  # 8-800 пропускаем


def test_mobile_wins_over_landline() -> None:
    """На мобильный отвечает сам руководитель, на стационарный — приёмная."""
    phones = parse_phones("(86150) 4-18-20, 8-918-432-0035")

    assert best_phone(phones).kind is PhoneKind.MOBILE


def test_duplicates_removed_within_cell() -> None:
    phones = parse_phones("8-918-432-0035, 8 918 432 00 35")

    assert len(phones) == 1


def test_seven_prefix_accepted() -> None:
    assert parse_phones("+7 918 432 00 35")[0].e164 == "+79184320035"


def test_empty_and_garbage() -> None:
    assert parse_phones(None) == []
    assert parse_phones("") == []
    assert parse_phones("None") == []
    assert parse_phones("нет данных") == []


def test_too_short_without_context_is_dropped() -> None:
    """Обрывок без кода города восстановить нельзя — лучше потерять, чем ошибиться."""
    assert parse_phones("4-47-07") == []


def test_best_phone_returns_none_when_only_toll_free() -> None:
    assert best_phone(parse_phones("8-800-505-0415")) is None
