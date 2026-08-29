"""Проверка окружения перед звонком.

    python -m grainvoice.doctor

Отвечает на вопрос «почему бот молчит». Причин обычно три, и различить их
по логу Pipecat трудно: в нём тишина от запрещённого микрофона выглядит
ровно так же, как тишина от молчащего человека.
"""

from __future__ import annotations

import array
import json
import math
import ssl
import sys
import urllib.error
import urllib.request

from pipecat.audio.vad.vad_analyzer import VAD_MIN_VOLUME

from grainvoice.audio import resolve_device
from grainvoice.certs import ensure_ca_bundle
from grainvoice.config import STTProvider, get_settings

#: Сколько секунд слушать микрофон.
_LISTEN_SECONDS = 5

#: Ниже этого уровня считаем, что звука нет вовсе: запрещённый микрофон
#: даёт ровные нули, разрешённый — хотя бы шум комнаты.
_SILENCE_RMS = 15

#: Сколько секунд звука нужно метрике BS.1770, чтобы вообще что-то измерить.
_LOUDNESS_WINDOW_SECS = 0.5


def _rms(data: bytes) -> int:
    """Среднеквадратичный уровень блока 16-битного звука.

    Считается вручную: модуль audioop из стандартной библиотеки удалён
    в Python 3.13, а сервер работает на 3.14.
    """
    if not data:
        return 0

    samples = array.array("h")
    samples.frombytes(data[: len(data) - len(data) % samples.itemsize])
    if not samples:
        return 0

    return int(math.sqrt(sum(s * s for s in samples) / len(samples)))


def _loudest_window(frames: list[bytes], rate: int) -> float | None:
    """Громкость самого громкого полусекундного окна по мерке Pipecat.

    Берётся максимум, а не среднее: собеседник говорит не всю запись,
    и пауза не должна занижать оценку.

    Returns:
        Громкость от 0 до 1 либо None, если измерить не удалось.
    """
    try:
        from pipecat.audio.utils import calculate_audio_volume
    except ImportError:
        return None

    window_bytes = int(_LOUDNESS_WINDOW_SECS * rate) * 2
    audio = b"".join(frames)
    if len(audio) < window_bytes:
        return None

    best = 0.0
    step = window_bytes // 2
    for start in range(0, len(audio) - window_bytes + 1, step):
        try:
            best = max(best, calculate_audio_volume(audio[start : start + window_bytes], rate))
        except Exception:
            return None
    return best


def _ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def _fail(msg: str) -> None:
    print(f"  \033[31m✗\033[0m {msg}")


def _warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}")


def check_microphone() -> bool:
    """Послушать микрофон и показать уровень сигнала.

    Returns:
        True, если звук приходит.
    """
    print(f"\nМикрофон — говорите вслух {_LISTEN_SECONDS} секунд:")

    try:
        import pyaudio
    except ImportError:
        _fail("pyaudio не установлен: pip install pyaudio (и brew install portaudio)")
        return False

    audio = pyaudio.PyAudio()
    try:
        chosen = resolve_device(get_settings().audio_input_device, want_input=True)
        device = (
            audio.get_device_info_by_index(chosen)
            if chosen is not None
            else audio.get_default_input_device_info()
        )
        print(f"  устройство: [{device['index']}] {device['name']}")

        rate, chunk = 16000, 1024
        # Слушаем ровно то устройство, которое пойдёт в звонок: проверять
        # системное «по умолчанию», когда в .env задано другое, бессмысленно.
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=rate,
            input=True,
            frames_per_buffer=chunk,
            input_device_index=resolve_device(get_settings().audio_input_device, want_input=True),
        )

        peak = 0
        frames: list[bytes] = []
        for _ in range(int(rate / chunk * _LISTEN_SECONDS)):
            data = stream.read(chunk, exception_on_overflow=False)
            frames.append(data)
            level = _rms(data)
            peak = max(peak, level)
            if sys.stdout.isatty():
                bar = "█" * min(40, level // 60)
                print(f"\r  уровень: {level:5}  {bar:<40}", end="", flush=True)

        stream.stop_stream()
        stream.close()
        print()

    except Exception as exc:
        print()
        _fail(f"не удалось открыть микрофон: {exc}")
        return False
    finally:
        audio.terminate()

    if peak < _SILENCE_RMS:
        _fail(f"звука нет (пик {peak})")
        print(
            "\n     Ровные нули почти всегда означают, что macOS не дала терминалу\n"
            "     доступ к микрофону. Системные настройки → Конфиденциальность\n"
            "     и безопасность → Микрофон → включить для вашего терминала.\n"
            "     После этого терминал нужно перезапустить."
        )
        return False

    # Решает не сырой уровень, а та же метрика громкости, по которой отсекает
    # определитель речи в конвейере. Иначе проверка говорит «звук есть» там,
    # где агент собеседника всё равно не услышит.
    volume = _loudest_window(frames, rate)
    if volume is None:
        _warn(f"пик {peak}, но громкость измерить не удалось")
        return True

    if volume < VAD_MIN_VOLUME:
        _fail(f"слишком тихо: громкость {volume:.2f} при пороге {VAD_MIN_VOLUME}")
        print(
            "\n     Звук идёт, но определитель речи его отбросит — агент будет\n"
            "     молчать в ответ. Что помогает:\n"
            "       • говорить ближе к микрофону;\n"
            "       • поднять громкость входа: Системные настройки → Звук → Вход;\n"
            "       • взять встроенный микрофон вместо Bluetooth-гарнитуры —\n"
            "         в режиме гарнитуры усиление заметно ниже.\n"
            "     Устройство задаётся в .env через AUDIO_INPUT_DEVICE."
        )
        return False

    _ok(f"звук идёт: пик {peak}, громкость {volume:.2f} при пороге {VAD_MIN_VOLUME}")
    return True


def list_devices() -> bool:
    """Показать все аудиоустройства с номерами.

    Номера нужны, чтобы задать AUDIO_INPUT_DEVICE и AUDIO_OUTPUT_DEVICE.
    Полагаться на «устройство по умолчанию» с наушниками нельзя: система
    переключает его при подключении, а PortAudio держит список, снятый
    при запуске процесса.
    """
    print("\nАудиоустройства:")
    try:
        import pyaudio
    except ImportError:
        _fail("pyaudio не установлен")
        return False

    settings = get_settings()
    audio = pyaudio.PyAudio()
    try:
        try:
            default_in = audio.get_default_input_device_info()["index"]
            default_out = audio.get_default_output_device_info()["index"]
        except Exception:
            default_in = default_out = None

        # Разрешаем один раз до цикла: иначе каждое устройство в списке
        # порождает свою пару записей в логе.
        chosen_in = resolve_device(settings.audio_input_device, want_input=True)
        chosen_out = resolve_device(settings.audio_output_device, want_input=False)

        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            roles = []
            if info["maxInputChannels"] > 0:
                roles.append("вход")
            if info["maxOutputChannels"] > 0:
                roles.append("выход")

            marks = []
            if index == default_in:
                marks.append("системный вход")
            if index == default_out:
                marks.append("системный выход")
            if index == chosen_in:
                marks.append("выбран как вход")
            if index == chosen_out:
                marks.append("выбран как выход")

            suffix = f"  ← {', '.join(marks)}" if marks else ""
            print(f"    [{index}] {info['name'][:38]:40} {'/'.join(roles):12}{suffix}")

        if default_in is None:
            _fail("система не сообщает устройство по умолчанию")
            return False

        if not settings.audio_input_device and not settings.audio_output_device:
            _warn(
                "устройства не заданы явно — при подключении наушников звук "
                "может уйти не туда. Пропишите номера в .env: "
                "AUDIO_INPUT_DEVICE и AUDIO_OUTPUT_DEVICE"
            )

        return True
    finally:
        audio.terminate()


def _request(url: str, headers: dict[str, str]) -> tuple[int, str]:
    """Выполнить GET и вернуть код с полным телом ответа.

    Тело читается целиком: обрезка ломает разбор JSON, а сообщение об ошибке
    тогда указывает не на настоящую причину.
    """
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=30, context=context
        ) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def _parse_json(body: str) -> dict:
    """Разобрать тело ответа, не роняя проверку на неожиданном формате."""
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def check_speech_provider() -> bool:
    """Проверить ключ распознавания и синтеза, и доступность голоса."""
    settings = get_settings()
    print(f"\nРечь — {settings.stt_provider.value}:")

    if settings.stt_provider is not STTProvider.ELEVENLABS:
        _warn("проверка написана для ElevenLabs, пропускаю")
        return True

    if not settings.elevenlabs_api_key:
        _fail("ELEVENLABS_API_KEY не задан")
        return False

    headers = {"xi-api-key": settings.elevenlabs_api_key}

    status, body = _request("https://api.elevenlabs.io/v1/user/subscription", headers)
    if status == 302:
        _fail("доступ заблокирован по IP — нужен VPN или другой хостинг")
        return False
    if status != 200:
        _fail(f"ключ отклонён ({status}): {body[:150]}")
        return False

    data = _parse_json(body)
    tier = data.get("tier", "?")
    used, limit = data.get("character_count", 0), data.get("character_limit", 0)
    _ok(f"ключ принят, тариф {tier}, символов {used}/{limit}")

    if limit and used >= limit:
        _fail("лимит символов исчерпан — синтез работать не будет")
        return False

    if not settings.tts_voice_id:
        _fail("TTS_VOICE_ID не задан")
        return False

    status, body = _request(
        f"https://api.elevenlabs.io/v1/voices/{settings.tts_voice_id}", headers
    )
    if status != 200:
        _fail(f"голос {settings.tts_voice_id} недоступен ({status}): {body[:150]}")
        return False

    voice = _parse_json(body)
    category = voice.get("category", "?")
    _ok(f"голос: {voice.get('name')} ({category})")

    if category != "premade" and tier == "free":
        _warn(
            "голос не из набора premade, а тариф бесплатный — "
            "синтез вернёт 402 «paid_plan_required»"
        )
        return False

    return True


def check_llm() -> bool:
    """Проверить ключ модели, ведущей разговор."""
    settings = get_settings()
    print(f"\nМодель — {settings.llm_provider.value}:")

    if not settings.llm_api_key:
        _fail("ключ провайдера не задан")
        return False

    if settings.llm_provider.value == "deepseek":
        url, headers = (
            f"{settings.deepseek_base_url.rstrip('/')}/models",
            {"Authorization": f"Bearer {settings.deepseek_api_key}"},
        )
    else:
        url, headers = (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
        )

    status, body = _request(url, headers)
    if status != 200:
        _fail(f"ключ отклонён ({status}): {body[:150]}")
        return False

    _ok(f"ключ принят, модель разговора {settings.conversation_model}")
    return True


def main() -> int:
    """Прогнать все проверки."""
    ensure_ca_bundle()
    print("Проверка окружения\n" + "=" * 50)

    results = [
        list_devices(),
        check_llm(),
        check_speech_provider(),
        check_microphone(),
    ]

    print("\n" + "=" * 50)
    if all(results):
        print("Всё в порядке — можно звонить:\n  python -m grainvoice.local")
        return 0

    print("Есть проблемы — см. отметки ✗ выше.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
