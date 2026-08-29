"""Настройка доверенных корневых сертификатов.

Python, установленный через Homebrew или с python.org, часто идёт без
привязки к системному хранилищу CA. Любое HTTPS-соединение тогда падает с
``CERTIFICATE_VERIFY_FAILED``, и сообщение выглядит как проблема доступа
к сервису, хотя дело в локальной установке Python.

Симптом обманчив: агент не слышит собеседника, и первая мысль — «не тот ключ»
или «провайдер недоступен». Поэтому проверка делается на старте, до звонка.
"""

from __future__ import annotations

import os
import ssl

from loguru import logger


def ensure_ca_bundle() -> None:
    """Подставить набор корневых сертификатов, если системный недоступен.

    Ничего не делает, если ``SSL_CERT_FILE`` уже задан или системное
    хранилище работает — чужую настройку не перетираем.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return

    if _system_store_works():
        return

    try:
        import certifi
    except ImportError:
        logger.warning(
            "Системное хранилище сертификатов недоступно, а certifi не установлен. "
            "HTTPS-соединения, скорее всего, будут падать."
        )
        return

    bundle = certifi.where()
    # Обе переменные: разные библиотеки читают разные.
    os.environ["SSL_CERT_FILE"] = bundle
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    logger.debug("Системное хранилище сертификатов недоступно, использую certifi")


def _system_store_works() -> bool:
    """Проверить, что стандартный контекст видит хоть какие-то корневые сертификаты."""
    try:
        context = ssl.create_default_context()
        stats = context.cert_store_stats()
    except Exception:
        return False

    return stats.get("x509_ca", 0) > 0
