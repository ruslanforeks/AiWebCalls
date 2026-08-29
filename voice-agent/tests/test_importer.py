"""Тесты импорта справочника.

Описания взяты дословно из выгрузки по Краснодарскому краю.
"""

from grainvoice.importer import LeadKind, classify, split_position


class TestClassify:
    """Классификация решает, тратить ли деньги на звонок."""

    def test_grain_crops(self) -> None:
        assert classify("рис, пшеница, подсолнечник, соя") is LeadKind.GRAIN
        assert classify("выращивание зерновых и масличных культур") is LeadKind.GRAIN

    def test_processor_beats_grain_keyword(self) -> None:
        """«производство рисовой крупы» — крупозавод, а не поставщик риса.

        Слово «рис» в описании есть, но звонить туда за зерном бессмысленно:
        они его покупают, а не продают.
        """
        assert classify("производство и реализация рисовой крупы") is LeadKind.PROCESSOR
        assert classify("хлеб, булочные, кондитерские изделия") is LeadKind.PROCESSOR

    def test_trader_beats_grain_keyword(self) -> None:
        """Закупщик — это конкурент, а не поставщик.

        «закупка масличных» и «приемка, хранение зерновых» содержат нужные
        слова, но звонить туда за зерном нельзя: там его покупают.
        """
        assert classify("закупка масличных, реализация масла") is LeadKind.TRADER
        assert classify("приемка, подработка, отгрузка, хранение зерновых") is LeadKind.TRADER
        assert LeadKind.TRADER.worth_calling is False

    def test_livestock(self) -> None:
        assert classify("крс (мясо-молочное)") is LeadKind.LIVESTOCK
        assert classify("переработка рыбы (копченая, соленая)") is LeadKind.PROCESSOR

    def test_other_agro(self) -> None:
        assert classify("плодоводство (яблоки)") is LeadKind.OTHER_AGRO
        assert classify("садоводство (персик, черешня)") is LeadKind.OTHER_AGRO

    def test_non_agro(self) -> None:
        assert classify("автоперевозки грузов") is LeadKind.NON_AGRO
        assert classify("подшипники всех типоразмеров") is LeadKind.NON_AGRO

    def test_empty_description_is_unknown_and_worth_calling(self) -> None:
        """Пустое описание — не повод пропускать: выясним звонком."""
        assert classify(None) is LeadKind.UNKNOWN
        assert classify("") is LeadKind.UNKNOWN
        assert LeadKind.UNKNOWN.worth_calling is True

    def test_only_grain_and_unknown_are_worth_calling(self) -> None:
        assert LeadKind.GRAIN.worth_calling is True
        assert LeadKind.PROCESSOR.worth_calling is False
        assert LeadKind.LIVESTOCK.worth_calling is False
        assert LeadKind.NON_AGRO.worth_calling is False


class TestSplitPosition:
    """В колонке «Имя» должность слита с ФИО."""

    def test_common_positions(self) -> None:
        assert split_position("Глава: Иванов Иван Иванович") == ("Глава", "Иванов Иван Иванович")
        assert split_position("Ген. дир.: Бабков Дмитрий Васильевич") == (
            "Ген. дир",
            "Бабков Дмитрий Васильевич",
        )
        assert split_position("Рук.: Алиджанов Константин Иванович") == (
            "Рук",
            "Алиджанов Константин Иванович",
        )

    def test_name_without_position(self) -> None:
        assert split_position("Иванов Иван Иванович") == (None, "Иванов Иван Иванович")

    def test_empty(self) -> None:
        assert split_position(None) == (None, None)
        assert split_position("None") == (None, None)
