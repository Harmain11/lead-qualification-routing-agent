from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lead_agent.application.integrations import begin_oauth_connection
from lead_agent.domain.integrations import PROVIDER_SETUP
from lead_agent.infrastructure.db import models  # noqa: F401
from lead_agent.infrastructure.db.base import Base
from lead_agent.infrastructure.oauth import DevelopmentOAuthBroker, ProviderOAuthBroker
from lead_agent.infrastructure.secrets import DevelopmentSecretStore
from lead_agent.settings.config import Settings


def database(tmp_path) -> Session:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'meta-oauth.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_meta_social_cards_are_oauth_first() -> None:
    assert PROVIDER_SETUP["instagram"].preferred_auth == "oauth2"
    assert PROVIDER_SETUP["messenger"].preferred_auth == "oauth2"
    assert PROVIDER_SETUP["whatsapp_business"].preferred_auth == "oauth2"
    for provider in ("instagram", "messenger", "whatsapp_business"):
        assert "oauth2" in PROVIDER_SETUP[provider].supported_auth


def test_development_broker_does_not_fake_meta_authorization() -> None:
    broker = DevelopmentOAuthBroker()
    with pytest.raises(ValueError, match="real provider OAuth credentials"):
        broker.begin("instagram", uuid4(), "state", "challenge", "http://localhost/callback", [])


def test_meta_oauth_uses_the_channel_app_and_comma_delimited_permissions(tmp_path) -> None:
    store = DevelopmentSecretStore()
    settings = Settings(
        meta_instagram_client_id="instagram-app-id",
        meta_instagram_client_secret="instagram-app-secret",
    )
    with database(tmp_path) as session:
        row, started = begin_oauth_connection(
            session,
            uuid4(),
            "instagram",
            "Instagram account",
            [],
            ProviderOAuthBroker(settings),
            store,
            "https://api.example.test",
            "actor-1",
        )

    parsed = urlparse(started.authorization_url)
    params = parse_qs(parsed.query)
    assert row.scopes == ["instagram_basic", "instagram_manage_messages", "pages_show_list"]
    assert params["client_id"] == ["instagram-app-id"]
    assert params["scope"] == ["instagram_basic,instagram_manage_messages,pages_show_list"]
    assert params["redirect_uri"] == [
        "https://api.example.test/v1/integrations/instagram/oauth/callback"
    ]
    assert params["code_challenge_method"] == ["S256"]


def test_meta_token_exchange_uses_graph_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "meta-user-token", "token_type": "bearer", "expires_in": 3600},
        )

    broker = ProviderOAuthBroker(
        Settings(
            meta_messenger_client_id="messenger-app-id",
            meta_messenger_client_secret="messenger-app-secret",
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    values = broker.exchange(
        "messenger",
        "authorization-code",
        "pkce-verifier",
        "https://api.example.test/v1/integrations/messenger/oauth/callback",
    )

    assert values["access_token"] == "meta-user-token"
    assert requests[0].url == "https://graph.facebook.com/v20.0/oauth/access_token"
    assert requests[0].method == "POST"
