"""Кэш готовых фраз для синтеза речи.

Агент повторяет одно и то же от звонка к звонку. Приветствие — дословно
каждый раз, а короткие реакции вроде «Понял.» и «Хорошо.» модель выдаёт
десятками. Синтезировать их заново полторы тысячи раз — платить за один
и тот же звук.

Выигрыш не только в деньгах, и деньги здесь не главное. Готовая запись
играет **мгновенно**: приветствие уходит в динамик без обращения к сети,
а это полсекунды в начале разговора — там, где собеседник решает,
слушать дальше или положить трубку.

Кэш адресуется содержимым: ключ считается от текста и всех параметров,
влияющих на звук. Меняете голос, модель или выразительность — кэш не
переиспользуется, и старую запись случайно не проиграют новым голосом.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

#: Куда складывать записи. Рядом с проектом, но вне репозитория.
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "tts"

#: Фразы длиннее этого не кэшируем. Длинные реплики модель почти никогда
#: не повторяет дословно, а место они занимают.
MAX_CACHEABLE_CHARS = 200

#: Длина порции, которой кэш отдаёт звук. Синтез шлёт поток мелкими
#: кусками, и вывод рассчитан на это: одну глыбу он проглотит, но ровно
#: проиграть не обязан.
_CHUNK_SECS = 0.02


def cache_key(text: str, *, voice: str, model: str, sample_rate: int, settings_fingerprint: str) -> str:
    """Ключ записи по содержимому и всему, что влияет на звук.

    В ключ входят не только текст и голос: выразительность и темп меняют
    звучание той же фразы, и старую запись нельзя проигрывать после смены
    настроек.

    Пробелы внутри фразы схлопываются. Приветствие в промпте записано
    в две строки для читаемости, а произносится одинаково — без этого
    заготовка и живая реплика расходятся ключами, и кэш молча не работает.
    """
    material = "\x00".join(
        [" ".join(text.split()), voice, model, str(sample_rate), settings_fingerprint]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class CachedElevenLabsTTS(ElevenLabsTTSService):
    """Синтез с кэшем: повторную фразу берёт с диска, новую сохраняет.

    Наследование, а не отдельный процессор в конвейере, выбрано сознательно:
    базовый класс сам ведёт учёт метрик, контекстов и кадров начала-конца
    речи. Подменяется только источник звука.
    """

    def __init__(self, *, cache_dir: Path | None = None, **kwargs) -> None:
        """
        Args:
            cache_dir: Куда складывать записи. По умолчанию .cache/tts
                рядом с проектом.
        """
        super().__init__(**kwargs)
        self._cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0

    def _fingerprint(self) -> str:
        """Слепок настроек, влияющих на звучание."""
        s = self._settings
        parts = [
            str(getattr(s, name, ""))
            for name in ("stability", "similarity_boost", "style", "speed", "use_speaker_boost")
        ]
        return "|".join(parts)

    def _path_for(self, text: str) -> Path:
        key = cache_key(
            text,
            voice=str(getattr(self._settings, "voice", "")),
            model=str(getattr(self._settings, "model", "")),
            sample_rate=self.sample_rate,
            settings_fingerprint=self._fingerprint(),
        )
        return self._cache_dir / f"{key}.pcm"

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Отдать звук: из кэша, если фраза уже звучала, иначе синтезировать."""
        cacheable = len(text.strip()) <= MAX_CACHEABLE_CHARS
        path = self._path_for(text) if cacheable else None

        if path is not None and path.exists():
            self._hits += 1
            logger.debug("Фраза из кэша: «{}»", text.strip()[:48])
            # Метрику времени до первого звука закрываем сразу: звук уже готов,
            # и без этого в отчёте останется незакрытый замер.
            await self.stop_ttfb_metrics()

            # Отдаём порциями, а не одной глыбой. Синтез шлёт звук потоком,
            # и вывод рассчитан именно на это: кадр в секунду длиной он
            # проглотит, но ровно проиграть не обязан. Здесь важно совпасть
            # с привычным для транспорта размером, а не сэкономить на вызовах.
            audio = path.read_bytes()
            # Шаг чётный: 16-битный отсчёт нельзя разрезать посередине,
            # иначе на стыке порций пойдёт треск.
            chunk = max(2, int(self.sample_rate * _CHUNK_SECS) * 2)
            for start in range(0, len(audio), chunk):
                yield TTSAudioRawFrame(
                    audio[start : start + chunk], self.sample_rate, 1, context_id=context_id
                )
            return

        self._misses += 1
        if cacheable:
            # Частоту пишем в лог намеренно: она входит в ключ, и расхождение
            # между заготовкой и живым вызовом — самая тихая из возможных
            # поломок. Кэш при этом «работает», просто никогда не попадает.
            logger.debug(
                "Синтезирую (в кэше нет, {} Гц): «{}»",
                self.sample_rate,
                text.strip()[:48],
            )

        chunks: list[bytes] = []
        async for frame in super().run_tts(text, context_id):
            if isinstance(frame, TTSAudioRawFrame):
                chunks.append(frame.audio)
            yield frame

        if path is not None and chunks:
            # Пишем через временный файл: если процесс убьют посреди записи,
            # в кэше не останется обрезанной фразы, которая потом проиграется
            # оборванной в живом звонке.
            tmp = path.with_suffix(".part")
            try:
                tmp.write_bytes(b"".join(chunks))
                tmp.replace(path)
            except OSError as exc:
                logger.warning("Не удалось сохранить фразу в кэш: {}", exc)
                tmp.unlink(missing_ok=True)

    @property
    def cache_stats(self) -> tuple[int, int]:
        """Сколько фраз взято из кэша и сколько синтезировано."""
        return self._hits, self._misses


async def warm_phrases(
    settings, phrases: list[str], *, sample_rate: int
) -> tuple[int, int]:
    """Заранее озвучить фразы и сложить в кэш.

    Обращается к API синтеза напрямую, минуя конвейер: поднимать вебсокет
    и весь Pipecat ради заготовки записей избыточно.

    Args:
        settings: Конфигурация с ключом, голосом и настройками звучания.
        phrases: Что озвучить.
        sample_rate: Частота, на которой будет работать конвейер. Обязательный
            параметр без умолчания: заготовка на чужой частоте не даст
            попаданий, а ошибка тихая — кэш просто не сработает,
            и это заметят только по счёту.

    Returns:
        Пара «записано, пропущено как уже существующие».
    """
    import json
    import ssl
    import urllib.error
    import urllib.request

    import certifi

    cache_dir = DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    fingerprint = "|".join(
        str(x)
        for x in (
            settings.tts_stability,
            settings.tts_similarity,
            settings.tts_style,
            settings.tts_speed,
            settings.tts_speaker_boost,
        )
    )
    context = ssl.create_default_context(cafile=certifi.where())
    written = skipped = 0

    for phrase in phrases:
        text = phrase.strip()
        if not text or len(text) > MAX_CACHEABLE_CHARS:
            continue

        key = cache_key(
            text,
            voice=settings.tts_voice_id,
            model=settings.tts_model,
            sample_rate=sample_rate,
            settings_fingerprint=fingerprint,
        )
        path = cache_dir / f"{key}.pcm"
        if path.exists():
            skipped += 1
            continue

        body = json.dumps(
            {
                "text": text,
                "model_id": settings.tts_model,
                "voice_settings": {
                    "stability": settings.tts_stability,
                    "similarity_boost": settings.tts_similarity,
                    "style": settings.tts_style,
                    "speed": settings.tts_speed,
                    "use_speaker_boost": settings.tts_speaker_boost,
                },
            }
        ).encode()
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}"
            f"?output_format=pcm_{sample_rate}"
        )
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "xi-api-key": settings.elevenlabs_api_key,
                "Content-Type": "application/json",
            },
        )

        try:
            audio = urllib.request.urlopen(request, timeout=60, context=context).read()
        except urllib.error.HTTPError as exc:
            logger.warning("«{}»: {} {}", text[:40], exc.code, exc.read()[:120])
            continue
        except OSError as exc:
            logger.warning("«{}»: {}", text[:40], exc)
            continue

        tmp = path.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(path)
        written += 1

    return written, skipped
