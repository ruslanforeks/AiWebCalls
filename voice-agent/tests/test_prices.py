"""Проверки прайса. Ошибка здесь — это деньги и обещание фермеру."""

import pytest

from grainvoice.prices import MIN_PROTEIN, format_price_table, quote_for_protein


class TestQuoteForProtein:
    """Цена подбирается по протеину."""

    @pytest.mark.parametrize(
        ("protein", "price"),
        [(14.0, 13000), (13.5, 12800), (13.0, 12600), (12.5, 12400), (12.0, 12200)],
    )
    def test_exact_tiers(self, protein: float, price: int) -> None:
        quote = quote_for_protein(protein)
        assert quote is not None
        assert quote.price == price
        assert quote.is_exact

    @pytest.mark.parametrize(
        ("protein", "price", "tier"),
        [(13.2, 12600, 13.0), (13.9, 12800, 13.5), (12.7, 12400, 12.5), (12.4, 12200, 12.0)],
    )
    def test_rounds_down_between_tiers(self, protein: float, price: int, tier: float) -> None:
        """Промежуточный протеин округляется вниз.

        Вверх округлять нельзя: компания заплатит за качество, которого
        в зерне нет, и разницу обнаружат на элеваторе.
        """
        quote = quote_for_protein(protein)
        assert quote is not None
        assert quote.price == price
        assert quote.tier == tier
        assert not quote.is_exact

    def test_above_top_tier_gets_top_price(self) -> None:
        """Сверх четырнадцати прайс не растёт — надбавку решает руководитель."""
        quote = quote_for_protein(15.5)
        assert quote is not None
        assert quote.price == 13000

    @pytest.mark.parametrize("protein", [11.9, 10.0, 0.0])
    def test_below_price_list_has_no_quote(self, protein: float) -> None:
        """Ниже прайса агент цену не называет."""
        assert quote_for_protein(protein) is None

    def test_unknown_protein_has_no_quote(self) -> None:
        """Без протеина цены нет — это главное правило разговора."""
        assert quote_for_protein(None) is None

    def test_min_protein_matches_lowest_tier(self) -> None:
        assert quote_for_protein(MIN_PROTEIN) is not None
        assert quote_for_protein(MIN_PROTEIN - 0.1) is None


class TestPriceTable:
    """Таблица уходит в промпт: агент читает её и называет цифры вслух."""

    def test_shows_both_tax_options(self) -> None:
        """Обе цифры нужны сразу: вопрос про НДС задаётся до цены."""
        table = format_price_table()
        assert "13000 без НДС" in table
        assert "14300 с НДС" in table

    def test_every_tier_present(self) -> None:
        table = format_price_table()
        for value in ("13000", "12800", "12600", "12400", "12200"):
            assert value in table

    def test_protein_written_with_comma(self) -> None:
        """Десятичная запятая, а не точка: модель читает это вслух."""
        assert "протеин 13,5" in format_price_table()


class TestVat:
    """НДС на зерно — 10%, и цена без пометки бессмысленна.

    Ставки в прайсе указаны БЕЗ НДС: так их дал заказчик.
    """

    def test_vat_is_ten_percent_not_twenty(self) -> None:
        """На зерно ставка льготная. Ошибка вдвое — это чужие деньги."""
        from grainvoice.prices import VAT_RATE

        assert VAT_RATE == 0.10

    def test_price_with_vat(self) -> None:
        quote = quote_for_protein(13.0)
        assert quote is not None
        assert quote.price == 12600
        assert quote.price_with_vat == 13860

    def test_for_vat_selects_correctly(self) -> None:
        quote = quote_for_protein(12.5)
        assert quote is not None
        assert quote.for_vat(with_vat=False) == 12400
        assert quote.for_vat(with_vat=True) == 13640

    def test_table_shows_both(self) -> None:
        """Агент читает таблицу вслух и должен видеть обе цифры."""
        table = format_price_table()
        assert "12600 без НДС" in table
        assert "13860 с НДС" in table
