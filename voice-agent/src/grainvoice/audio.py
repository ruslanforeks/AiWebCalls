"""Выбор аудиоустройств для локального разговора.

PortAudio нумерует устройства в порядке, который меняется при подключении
и отключении наушников: сегодня AirPods это номер 3, завтра — номер 1.
Поэтому устройство можно задать и названием — оно стабильнее.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True)
class AudioDevice:
    """Устройство ввода или вывода звука."""

    index: int
    name: str
    max_input_channels: int
    max_output_channels: int

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0


def list_audio_devices() -> list[AudioDevice]:
    """Перечислить доступные устройства.

    Returns:
        Список устройств; пустой, если PortAudio недоступен.
    """
    try:
        import pyaudio
    except ImportError:
        return []

    audio = pyaudio.PyAudio()
    try:
        return [
            AudioDevice(
                index=i,
                name=str(audio.get_device_info_by_index(i)["name"]),
                max_input_channels=int(audio.get_device_info_by_index(i)["maxInputChannels"]),
                max_output_channels=int(audio.get_device_info_by_index(i)["maxOutputChannels"]),
            )
            for i in range(audio.get_device_count())
        ]
    finally:
        audio.terminate()


def resolve_device(value: str | None, *, want_input: bool) -> int | None:
    """Превратить настройку в номер устройства PortAudio.

    Args:
        value: Номер («3») или часть названия («AirPods»). Пусто — системное
            устройство по умолчанию.
        want_input: Ищем устройство ввода или вывода. Важно, потому что одни
            и те же наушники присутствуют в списке дважды — отдельно как
            микрофон и отдельно как динамик.

    Returns:
        Номер устройства либо None, если оставляем выбор системе.
    """
    if value is None or not str(value).strip():
        return None

    value = str(value).strip()

    if value.lstrip("-").isdigit():
        return int(value)

    devices = [d for d in list_audio_devices() if (d.is_input if want_input else d.is_output)]
    needle = value.casefold()
    matches = [d for d in devices if needle in d.name.casefold()]

    role = "ввода" if want_input else "вывода"
    if not matches:
        available = ", ".join(f"[{d.index}] {d.name}" for d in devices) or "нет устройств"
        logger.warning(
            "Устройство {} по запросу «{}» не найдено, беру системное. Доступны: {}",
            role,
            value,
            available,
        )
        return None

    if len(matches) > 1:
        logger.warning(
            "Под «{}» подходит несколько устройств {}, беру первое: [{}] {}",
            value,
            role,
            matches[0].index,
            matches[0].name,
        )

    logger.debug("Устройство {}: [{}] {}", role, matches[0].index, matches[0].name)
    return matches[0].index


#: Частоты, которые умеет отдавать синтез ElevenLabs. Просить у него
#: произвольное число нельзя — вернётся 24000 и молчаливый пересчёт.
SUPPORTED_OUTPUT_RATES = (8000, 16000, 22050, 24000, 32000, 44100, 48000)


def native_output_rate(device_index: int | None) -> int | None:
    """Родная частота устройства вывода, если её поддерживает синтез.

    Смысл в том, чтобы звук нигде не пересчитывался. Встроенный выход
    работает на 44100, AirPods — на 48000; если просить у синтеза одно,
    а устройство ждёт другого, пересчёт делает система на лету, и на
    загруженной машине это слышно как хрип.

    Returns:
        Частота либо None, если устройство неизвестно или его частоту
        синтез не умеет — тогда решение остаётся за вызывающим.
    """
    devices = list_audio_devices()
    if not devices:
        return None

    if device_index is None:
        # Устройство по умолчанию: берём первое, умеющее выводить звук.
        candidates = [d for d in devices if d.is_output]
    else:
        candidates = [d for d in devices if d.index == device_index]

    if not candidates:
        return None

    rate = _device_rate(candidates[0].index)
    if rate in SUPPORTED_OUTPUT_RATES:
        logger.debug("Родная частота устройства вывода: {} Гц", rate)
        return rate

    return None


def _device_rate(index: int) -> int | None:
    """Заявленная частота устройства."""
    try:
        import pyaudio
    except ImportError:
        return None

    audio = pyaudio.PyAudio()
    try:
        return int(audio.get_device_info_by_index(index)["defaultSampleRate"])
    except Exception:
        return None
    finally:
        audio.terminate()
