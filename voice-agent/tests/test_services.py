

class TestVoiceSettings:
    """Настройки голоса должны доезжать до синтеза.

    Пропущенные настройки — не мелочь: при style=0.0 голос звучит ровной
    дикторской начиткой, и это ровно тот «робот», из-за которого продукт
    выглядит дешёвым. Ошибка незаметна в коде и слышна только на слух,
    поэтому её стережёт тест.
    """

    def test_style_reaches_the_service(self) -> None:
        from grainvoice.config import Settings
        from grainvoice.services import build_tts

        settings = Settings(
            ELEVENLABS_API_KEY="test-key",
            TTS_VOICE_ID="voice-1",
            TTS_STYLE=0.4,
            TTS_SPEED=0.9,
        )
        service = build_tts(settings)

        assert service._settings.style == 0.4
        assert service._settings.speed == 0.9

    def test_defaults_are_not_flat(self) -> None:
        """Значения по умолчанию не должны обнулять выразительность."""
        from grainvoice.config import Settings

        assert Settings().tts_style > 0
