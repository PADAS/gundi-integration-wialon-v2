import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

import app.actions.client as client


@pytest.mark.asyncio
async def test_get_authentication_token_success():
    response_mock = MagicMock()
    response_mock.json.return_value = {"eid": "token123"}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        token = await client.get_authentication_token(
            base_url=None,
            token="secret_token"
        )
        assert token == "token123"


@pytest.mark.asyncio
async def test_get_authentication_token_with_custom_base_url():
    response_mock = MagicMock()
    response_mock.json.return_value = {"eid": "token456"}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)) as post_mock:
        token = await client.get_authentication_token(
            base_url="https://custom.wialon.com/",
            token="secret_token"
        )
        assert token == "token456"

    post_mock.assert_awaited_once()
    assert post_mock.await_args.args[0].startswith("https://custom.wialon.com/")


@pytest.mark.asyncio
async def test_get_authentication_token_http_error():
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.HTTPError("fail"))):
        with pytest.raises(httpx.HTTPError):
            await client.get_authentication_token(
                base_url=None,
                token="secret_token"
            )


@pytest.mark.asyncio
async def test_get_authentication_token_wialon_error():
    response_mock = MagicMock()
    response_mock.json.return_value = {"error": 4, "reason": "Invalid token"}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        with pytest.raises(client.WialonErrorException):
            await client.get_authentication_token(
                base_url=None,
                token="invalid_token"
            )


@pytest.mark.asyncio
async def test_get_authentication_token_invalid_auth_token():
    response_mock = MagicMock()
    response_mock.json.return_value = {"error": 8, "reason": "Invalid token"}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        with pytest.raises(client.WialonInvalidAuthTokenException):
            await client.get_authentication_token(
                base_url=None,
                token="invalid_token"
            )


@pytest.mark.asyncio
async def test_get_authentication_token_user_not_found_is_an_invalid_token():
    """What prod actually sees for a dead token: error 4 with reason
    TOKEN_USER_NOT_FOUND. It must be classified as an invalid token so the
    handler logs the operator-facing activity entry, not a generic error."""
    response_mock = MagicMock()
    response_mock.json.return_value = {"error": 4, "reason": "TOKEN_USER_NOT_FOUND"}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        with pytest.raises(client.WialonInvalidAuthTokenException) as excinfo:
            await client.get_authentication_token(base_url=None, token="dead_token")

    assert "TOKEN_USER_NOT_FOUND" in str(excinfo.value)


@pytest.mark.asyncio
async def test_get_positions_list_success():
    response_mock = MagicMock()
    response_mock.json.return_value = {
        "items": [
            {"nm": "Vehicle 1", "id": 1, "pos": None},
            {"nm": "Vehicle 2", "id": 2, "pos": {"t": 1234567890, "y": 10.0, "x": 20.0, "c": 0, "z": 0, "s": 0, "sc": 0, "f": 0}}
        ]
    }
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        result = await client.get_positions_list(
            base_url=None,
            session_id="valid_session_id"
        )
        assert hasattr(result, "items")
        assert isinstance(result.items, list)
        assert len(result.items) == 2
        assert result.items[0].nm == "Vehicle 1"
        assert result.items[1].nm == "Vehicle 2"


@pytest.mark.asyncio
async def test_get_positions_list_invalid_session():
    response_mock = MagicMock()
    response_mock.json.return_value = {"error": 1}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        with pytest.raises(client.WialonInvalidSessionException):
            await client.get_positions_list(
                base_url=None,
                session_id="invalid_session_id"
            )


@pytest.mark.asyncio
async def test_get_positions_list_wialon_error_exception():
    response_mock = MagicMock()
    response_mock.json.return_value = {"error": 5}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        with pytest.raises(client.WialonErrorException) as exc:
            await client.get_positions_list(
                base_url=None,
                session_id="valid_session_id"
            )
        assert "Error 5 occurred while fetching positions" in str(exc.value)


@pytest.mark.asyncio
async def test_get_positions_list_http_error():
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.HTTPError("fail"))):
        with pytest.raises(httpx.HTTPError):
            await client.get_positions_list(
                base_url=None,
                session_id="valid_session_id"
            )


@pytest.mark.asyncio
async def test_get_positions_list_empty_items():
    response_mock = MagicMock()
    response_mock.json.return_value = {"items": []}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)):
        result = await client.get_positions_list(
            base_url=None,
            session_id="valid_session_id"
        )
        assert result.items == []


@pytest.mark.asyncio
async def test_wialon_calls_use_the_shared_timeout():
    """core/search_items on a large account is slow; the refactor cut the
    timeout from 120 s to 10 s. Both calls take it from one constant."""
    assert client.WIALON_TIMEOUT_SECONDS == 60

    response_mock = MagicMock()
    response_mock.json.return_value = {"eid": "token123"}
    response_mock.raise_for_status.return_value = None
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.post = AsyncMock(return_value=response_mock)

    with patch("app.actions.client.httpx.AsyncClient", return_value=fake_client) as client_cls:
        await client.get_authentication_token(base_url=None, token="secret_token")
        response_mock.json.return_value = {"items": []}
        await client.get_positions_list(base_url=None, session_id="token123")

    assert [c.kwargs.get("timeout") for c in client_cls.call_args_list] == [60, 60]
