"""Exercise SDK connection paths without credentials or live network."""

import asyncio
import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock

import botpy.gateway
import botpy.http
import pytest

from lib.qq_sdk_security import configure_qq_sdk_security


@pytest.mark.parametrize("transport", ["http", "websocket"])
def test_sdk_connections_verify_certificates_and_hostname(monkeypatch, transport):
    # Restore the upstream factory so this test also catches a missing startup fix.
    monkeypatch.setattr(botpy.http, "SSLContext", ssl.SSLContext)
    monkeypatch.setattr(botpy.gateway, "SSLContext", ssl.SSLContext)
    configure_qq_sdk_security()
    configure_qq_sdk_security()
    contexts = []

    class ConnectorReached(Exception):
        pass

    def connector(**kwargs):
        contexts.append(kwargs["ssl"])
        raise ConnectorReached

    async def run():
        if transport == "http":
            monkeypatch.setattr(botpy.http, "TCPConnector", connector)
            client = botpy.http.BotHttp(timeout=5)
            client._token = SimpleNamespace(
                check_token=AsyncMock(), get_string=lambda: "synthetic-token", app_id="test",
            )
            await client.check_session()
        else:
            monkeypatch.setattr(botpy.gateway, "TCPConnector", connector)
            client = object.__new__(botpy.gateway.BotWebSocket)
            client._session = {"url": "wss://example.invalid"}
            await client.ws_connect()

    with pytest.raises(ConnectorReached):
        asyncio.run(run())
    assert len(contexts) == 1
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert contexts[0].check_hostname is True
    assert contexts[0].cert_store_stats()["x509_ca"] > 0
