import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

import app.actions.client as client

@pytest.mark.asyncio
async def test_get_authentication_token_success():
    integration = MagicMock()
    integration.id = 1
    integration.base_url = None
    config = MagicMock()
    config.token.get_secret_value.return_value = "secret"
    response_mock = MagicMock()
    response_mock.json.return_value = {"eid": "token123"}
    response_mock.raise_for_status.return_value = None

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response_mock)), \
         patch.object(client.state_manager, "set_state", new=AsyncMock()) as set_state_mock:
        token = await client.get_authentication_token(integration, config)
        assert token == "token123"
        set_state_mock.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_authentication_token_http_error():
    integration = MagicMock()
    integration.id = 1
    integration.base_url = None
    config = MagicMock()
    config.token.get_secret_value.return_value = "secret"
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.HTTPError("fail"))):
        with pytest.raises(httpx.HTTPError):
            await client.get_authentication_token(integration, config)

@pytest.mark.asyncio
async def test_build_request_params_with_saved_token():
    integration = MagicMock()
    integration.id = 1
    with patch.object(client.state_manager, "get_state", new=AsyncMock(return_value={"eid": "token123"})), \
         patch("app.actions.client.get_auth_config", return_value=MagicMock()):
        params = await client.build_request_params(integration)
        assert params["sid"] == "token123"
        assert "params" in params

@pytest.mark.asyncio
async def test_build_request_params_without_saved_token():
    integration = MagicMock()
    integration.id = 1
    with patch.object(client.state_manager, "get_state", new=AsyncMock(return_value=None)), \
         patch("app.actions.client.get_auth_config", return_value=MagicMock()), \
         patch("app.actions.client.get_authentication_token", new=AsyncMock(return_value="token456")):
        params = await client.build_request_params(integration)
        assert params["sid"] == "token456"

@pytest.mark.asyncio
async def test_get_positions_list_success():
    integration = MagicMock()
    integration.id = 1
    with patch("app.actions.client.build_request_params", new=AsyncMock(return_value={"params": "{}", "sid": "token"})), \
         patch("httpx.AsyncClient.post", new=AsyncMock()) as post_mock:
        response_mock = MagicMock()
        response_mock.json.return_value = {"items": [{"device_name": "dev", "device_id": 1, "device_last_position": {}}]}
        response_mock.raise_for_status.return_value = None
        post_mock.return_value = response_mock
        result = await client.get_positions_list(integration)
        assert hasattr(result, "items")
        assert isinstance(result.items, list)

@pytest.mark.asyncio
async def test_get_positions_list_invalid_session():
    integration = MagicMock()
    integration.id = 1
    with patch("app.actions.client.build_request_params", new=AsyncMock(return_value={"params": "{}", "sid": "token"})), \
         patch("httpx.AsyncClient.post", new=AsyncMock()) as post_mock, \
         patch.object(client.state_manager, "delete_state", new=AsyncMock()) as delete_state_mock:
        response_mock = MagicMock()
        response_mock.json.return_value = {"error": 1}
        response_mock.raise_for_status.return_value = None
        post_mock.return_value = response_mock
        with pytest.raises(client.WialonInvalidSessionException):
            await client.get_positions_list(integration)
        delete_state_mock.assert_awaited()

@pytest.mark.asyncio
async def test_get_positions_list_wialon_error_exception():
    integration = MagicMock()
    integration.id = 1
    with patch("app.actions.client.build_request_params", new=AsyncMock(return_value={"params": "{}", "sid": "token"})), \
            patch("httpx.AsyncClient.post", new=AsyncMock()) as post_mock:
        response_mock = MagicMock()
        response_mock.json.return_value = {"error": 5}
        response_mock.raise_for_status.return_value = None
        post_mock.return_value = response_mock
        with pytest.raises(client.WialonErrorException) as exc:
            await client.get_positions_list(integration)
        assert "Error 5 occurred while fetching positions" in str(exc.value)

@pytest.mark.asyncio
async def test_get_positions_list_http_error():
    integration = MagicMock()
    integration.id = 1
    with patch("app.actions.client.build_request_params", new=AsyncMock(return_value={"params": "{}", "sid": "token"})), \
         patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.HTTPError("fail"))):
        with pytest.raises(httpx.HTTPError):
            await client.get_positions_list(integration)
