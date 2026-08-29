"""Проверки карточки хозяйства."""

from grainvoice.farm import FarmContext


class TestKeyterms:
    """Подсказки распознаванию строятся под конкретный звонок.

    Без подсказки «Белоглинский» слышится как «минский» или «янтарский»:
    название редкое, а вариантов у модели много.
    """

    def test_district_comes_first(self) -> None:
        """Район важнее общего словаря: его произносят почти в каждом звонке."""
        terms = FarmContext(district="Белоглинский район").keyterms()
        assert terms[0] == "Белоглинский"

    def test_strips_service_suffixes(self) -> None:
        """В справочнике «Белоглинский район», а произносят одним словом."""
        assert "Белоглинский" in FarmContext(district="Белоглинский район").keyterms()
        assert "Краснодар" in FarmContext(district="Краснодар г.").keyterms()

    def test_grain_vocabulary_always_present(self) -> None:
        terms = FarmContext().keyterms()
        assert "протеин" in terms
        assert "клейковина" in terms

    def test_vat_terms_present(self) -> None:
        """Про НДС агент спрашивает в каждом звонке.

        Без подсказки «без НДС» слышится как «бизнес» — так и вышло
        на живом разговоре, и агент переспросил впустую.
        """
        terms = FarmContext().keyterms()
        for term in ("НДС", "без НДС", "с НДС"):
            assert term in terms, f"«{term}» пропал из подсказок"

    def test_no_duplicates(self) -> None:
        """Культура из карточки не должна дублировать общий словарь."""
        terms = FarmContext(known_crops=["пшеница", "пшеница"]).keyterms()
        assert terms.count("пшеница") == 1


class TestDescribe:
    """Описание уходит в промпт, чтобы агент не спрашивал известное."""

    def test_empty_when_nothing_known(self) -> None:
        assert FarmContext().describe() == ""

    def test_mentions_crops_from_base(self) -> None:
        text = FarmContext(known_crops=["пшеница", "подсолнечник"]).describe()
        assert "пшеница, подсолнечник" in text

    def test_partial_card_still_useful(self) -> None:
        """Знаем только район — этого уже достаточно для подсказки."""
        text = FarmContext(district="Белоглинский район").describe()
        assert "Белоглинский" in text
