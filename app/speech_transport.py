"""Use the platform proxy and trust store for verified Edge speech requests.

Edge TTS 7 creates private, module-level SSL contexts from certifi alone and
passes them explicitly to aiohttp, overriding connector trust configuration.
Adding the platform's default certificates to those existing contexts supports
managed HTTPS proxies while retaining certifi roots, certificate verification,
and hostname checking. No certificate bundle is modified on disk.
"""

import os
import ssl
import threading
import weakref

import edge_tts.communicate
import edge_tts.voices

_lock = threading.Lock()
_configured_contexts: weakref.WeakSet = weakref.WeakSet()


def configure_voice_transport() -> str | None:
    """Add system trust once per context; return the configured proxy privately.

    The return value may contain authentication and must not be logged or saved
    in project metadata. Missing private contexts are tolerated for compatible
    future releases; only real SSL contexts are changed, additively.
    """
    with _lock:
        for module in (edge_tts.communicate, edge_tts.voices):
            context = getattr(module, "_SSL_CTX", None)
            if isinstance(context, ssl.SSLContext) and context not in _configured_contexts:
                context.load_default_certs()
                _configured_contexts.add(context)
    return (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or None
    )
