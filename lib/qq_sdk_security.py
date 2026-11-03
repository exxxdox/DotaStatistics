"""Keep qq-botpy 1.2 connection defaults safe without editing installed packages."""

import ssl

import botpy.gateway
import botpy.http


def configure_qq_sdk_security() -> None:
    # SDK 1.2 uses unverified SSLContext() in both transports; replace only its local factories.
    botpy.http.SSLContext = ssl.create_default_context
    botpy.gateway.SSLContext = ssl.create_default_context
