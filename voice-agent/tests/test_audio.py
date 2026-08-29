"""Проверки выбора аудиоустройства."""

from unittest.mock import patch

from grainvoice.audio import AudioDevice, resolve_device

# Слепок реального Mac с подключёнными AirPods: наушники присутствуют
# дважды — отдельно микрофоном, отдельно динамиком.
_DEVICES = [
    AudioDevice(0, "Микрофон (iPhoneR)", 1, 0),
    AudioDevice(1, "Встроенный микрофон", 2, 0),
    AudioDevice(2, "Встроенный выход", 0, 2),
    AudioDevice(3, "AirPods Pro (Руслан)", 1, 0),
    AudioDevice(4, "AirPods Pro (Руслан)", 0, 2),
]


class TestResolveDevice:
    """Название устойчивее номера: нумерация меняется при подключении гарнитуры."""

    def test_empty_means_system_default(self) -> None:
        assert resolve_device("", want_input=True) is None
        assert resolve_device(None, want_input=True) is None

    def test_numeric_passes_through(self) -> None:
        assert resolve_device("3", want_input=True) == 3

    def test_name_picks_right_side(self) -> None:
        """Одно название, два устройства — вход и выход не должны путаться."""
        with patch("grainvoice.audio.list_audio_devices", return_value=_DEVICES):
            assert resolve_device("AirPods", want_input=True) == 3
            assert resolve_device("AirPods", want_input=False) == 4

    def test_name_is_case_insensitive(self) -> None:
        with patch("grainvoice.audio.list_audio_devices", return_value=_DEVICES):
            assert resolve_device("airpods", want_input=True) == 3

    def test_unknown_name_falls_back_to_default(self) -> None:
        """Опечатка в названии не должна ронять звонок."""
        with patch("grainvoice.audio.list_audio_devices", return_value=_DEVICES):
            assert resolve_device("Наушники Sony", want_input=True) is None

    def test_builtin_still_reachable(self) -> None:
        with patch("grainvoice.audio.list_audio_devices", return_value=_DEVICES):
            assert resolve_device("Встроенн", want_input=True) == 1
            assert resolve_device("Встроенн", want_input=False) == 2


class TestMicLevelThreshold:
    """Порог проверки микрофона должен совпадать с порогом конвейера.

    Иначе диагностика говорит «звук есть» там, где определитель речи
    его отбросит, и агент молчит без объяснимой причины.
    """

    def test_threshold_comes_from_pipecat(self) -> None:
        from pipecat.audio.vad.vad_analyzer import VAD_MIN_VOLUME

        from grainvoice import doctor

        # Проверка не должна заводить собственную константу порога.
        assert doctor.VAD_MIN_VOLUME is VAD_MIN_VOLUME

    def test_loudest_window_prefers_peak_over_average(self) -> None:
        """Пауза в записи не должна занижать оценку."""
        import numpy as np

        from grainvoice.doctor import _loudest_window

        rate = 16000
        rng = np.random.default_rng(0)
        loud = rng.normal(0, 2000, rate).clip(-32767, 32767).astype(np.int16).tobytes()
        silence = np.zeros(rate * 3, dtype=np.int16).tobytes()

        assert _loudest_window([silence, loud, silence], rate) > 0.6


class TestNativeOutputRate:
    """Частота синтеза подбирается под устройство.

    Полностью избежать пересчёта нельзя: родные 44100 и 48000 синтез отдаёт
    только с тарифа Pro. Но ровное удвоение система выполняет чисто,
    а дробное отношение даёт слышимые призвуки.
    """

    def _with(self, device_rate: int):
        from unittest.mock import patch

        from grainvoice.audio import AudioDevice, native_output_rate

        devices = [AudioDevice(1, "Устройство", 0, 2)]
        with patch("grainvoice.audio.list_audio_devices", return_value=devices), \
             patch("grainvoice.audio._device_rate", return_value=device_rate):
            return native_output_rate(1)

    def test_builtin_output_gets_half(self) -> None:
        """Встроенный выход 44100 → 22050, ровно вдвое."""
        assert self._with(44100) == 22050

    def test_airpods_gets_half(self) -> None:
        """AirPods 48000 → 24000, ровно вдвое."""
        assert self._with(48000) == 24000

    def test_telephony_rate_used_directly(self) -> None:
        """Телефонные 16000 синтез отдаёт как есть."""
        assert self._with(16000) == 16000

    def test_high_tier_rates_not_requested(self) -> None:
        """44100 и 48000 напрямую просить нельзя: на Creator придёт 403,
        и агент будет беззвучно шевелить губами."""
        from grainvoice.audio import SUPPORTED_OUTPUT_RATES

        assert 44100 not in SUPPORTED_OUTPUT_RATES
        assert 48000 not in SUPPORTED_OUTPUT_RATES

    def test_odd_rate_defers_to_caller(self) -> None:
        assert self._with(37000) is None

    def test_no_devices_returns_none(self) -> None:
        from unittest.mock import patch

        from grainvoice.audio import native_output_rate

        with patch("grainvoice.audio.list_audio_devices", return_value=[]):
            assert native_output_rate(None) is None
