import pytest
import httpx
import datetime
from unittest.mock import AsyncMock, patch, MagicMock

import app.actions.handlers as handlers
import app.actions.client as client

@pytest.mark.asyncio
async def test_action_auth_success():
    integration = MagicMock()
    config = MagicMock()
    with patch('app.actions.client.get_authentication_token', new=AsyncMock(return_value='token')):
        result = await handlers.action_auth(integration, config)
        assert result == {"valid_credentials": True}

@pytest.mark.asyncio
async def test_action_auth_failure_returns_error():
    integration = MagicMock()
    config = MagicMock()
    with patch('app.actions.client.get_authentication_token', new=AsyncMock(side_effect=client.WialonErrorException("error"))):
        result = await handlers.action_auth(integration, config)
        assert result["valid_credentials"] is False
        assert "error" in result

@pytest.mark.asyncio
async def test_action_fetch_samples_success():
    integration = MagicMock()
    config = MagicMock(observations_to_extract=2)
    vehicle1 = MagicMock()
    vehicle1.json.return_value = '{"id": 1}'
    vehicle2 = MagicMock()
    vehicle2.json.return_value = '{"id": 2}'
    vehicles = MagicMock(items=[vehicle1, vehicle2])
    with patch('app.actions.client.get_positions_list', new=AsyncMock(return_value=vehicles)):
        result = await handlers.action_fetch_samples(integration, config)
        assert result["observations_extracted"] == 2
        assert result["observations"] == [{"id": 1}, {"id": 2}]

@pytest.mark.asyncio
async def test_action_fetch_samples_failure():
    integration = MagicMock()
    config = MagicMock()
    with patch('app.actions.client.get_positions_list', new=AsyncMock(side_effect=httpx.HTTPError("error"))):
        with pytest.raises(httpx.HTTPError):
            await handlers.action_fetch_samples(integration, config)

@pytest.mark.asyncio
async def test_action_fetch_samples_wialon_error_exception():
    integration = MagicMock()
    config = MagicMock()
    with patch('app.actions.client.get_positions_list',
               new=AsyncMock(side_effect=client.WialonErrorException("Error 5 occurred while fetching positions"))):
        with pytest.raises(client.WialonErrorException) as exc:
            await handlers.action_fetch_samples(integration, config)
        assert "Error 5 occurred while fetching positions" in str(exc.value)

@pytest.mark.asyncio
async def test_action_pull_observations_success():
    integration = MagicMock()
    integration.id = 123
    config = MagicMock()
    vehicle = MagicMock()
    vehicle.id = "dev1"
    vehicle.nm = "Device 1"
    pos = MagicMock()
    pos.dict.return_value = {
        "recorded_at": "2024-01-01 12:00:00+0000",
        "latitude": 1.0,
        "longitude": 2.0,
        "t": datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    }
    vehicle.pos = pos
    vehicles = MagicMock(items=[vehicle])

    with patch('app.actions.client.get_positions_list', new=AsyncMock(return_value=vehicles)), \
         patch('app.actions.handlers.state_manager.get_state', new=AsyncMock(return_value=None)), \
         patch('app.actions.handlers.send_observations_to_gundi', new=AsyncMock(return_value={"status": "ok"})), \
         patch('app.actions.handlers.state_manager.set_state', new=AsyncMock()):
        result = await handlers.action_pull_observations(integration, config)
        assert result["observations_extracted"] == 1
        assert result["details"] == {"status": "ok"}

@pytest.mark.asyncio
async def test_action_pull_observations_no_data():
    integration = MagicMock()
    integration.id = 123
    config = MagicMock()
    vehicles = MagicMock(items=[])

    with patch('app.actions.client.get_positions_list', new=AsyncMock(return_value=vehicles)):
        result = await handlers.action_pull_observations(integration, config)
        assert result["observations_extracted"] == 0
        assert "No transformed data" in result["details"]

@pytest.mark.asyncio
async def test_action_pull_observations_http_error():
    integration = MagicMock()
    integration.id = 123
    config = MagicMock()
    with patch('app.actions.client.get_positions_list', new=AsyncMock(side_effect=httpx.HTTPError("error"))):
        result = await handlers.action_pull_observations(integration, config)
        assert result["details"] == 'pull_observations action returned error.'
        assert "error" in result

