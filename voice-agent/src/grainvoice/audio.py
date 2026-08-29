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


#: Частоты, которые синтез отдаёт на любом тарифе.
#:
#: Формально ElevenLabs поддерживает и 44100, и 48000, но сырой звук в этих
#: частотах открыт только с тарифа Pro: на Creator приходит 403
#: «subscription_required». Молча просить недоступное нельзя — синтез просто
#: не выдаст звука, и агент будет беззвучно шевелить губами.
SUPPORTED_OUTPUT_RATES = (8000, 16000, 22050, 24000)


def native_output_rate(device_index: int | None) -> int | None:
    """Частота синтеза, ровно ложащаяся на частоту устройства вывода.

    Полностью избежать пересчёта не выходит: родные 44100 и 48000 синтез
    отдаёт только с тарифа Pro. Но пересчёт пересчёту рознь — вдвое он
    ровный, а в дробном отношении даёт слышимые призвуки.

    Поэтому берётся наибольшая доступная частота, делящая частоту
    устройства нацело: под встроенный выход 44100 это 22050, под AirPods
    48000 — 24000. Ровное удвоение система выполняет чисто.

    Returns:
        Частота либо None, если устройство неизвестно или подходящей
        частоты не нашлось — тогда решение остаётся за вызывающим.
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
    if rate is None:
        return None

    if rate in SUPPORTED_OUTPUT_RATES:
        logger.debug("Частота устройства {} Гц доступна синтезу напрямую", rate)
        return rate

    divisors = [r for r in SUPPORTED_OUTPUT_RATES if rate % r == 0]
    if divisors:
        chosen = max(divisors)
        logger.debug(
            "Устройство на {} Гц, синтез на {} Гц — ровное удвоение", rate, chosen
        )
        return chosen

    logger.debug("Частота устройства {} Гц не делится нацело, решает вызывающий", rate)
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


def device_native_rate(device_index: int | None) -> int | None:
    """Родная частота устройства вывода, какой бы она ни была.

    В отличие от native_output_rate здесь нет оглядки на то, что умеет
    синтез: поток к устройству открывается на его собственной частоте,
    чтобы система ничего не преобразовывала. Её преобразование грубое,
    простым удвоением отсчётов, и это слышно как призвуки — качественный
    пересчёт делает Pipecat через soxr.
    """
    devices = list_audio_devices()
    if not devices:
        return None

    if device_index is None:
        candidates = [d for d in devices if d.is_output]
    else:
        candidates = [d for d in devices if d.index == device_index]

    return _device_rate(candidates[0].index) if candidates else None
