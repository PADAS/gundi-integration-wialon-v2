import inspect
import pytest
import httpx
import datetime
from unittest.mock import AsyncMock, patch, MagicMock

import backoff
import app.actions.handlers as handlers
import app.actions.client as client
from app.actions.configurations import AuthenticateConfig


@pytest.fixture
def mock_integration():
    """Create a mock integration with proper configuration structure."""
    integration = MagicMock()
    integration.id = "test-integration-123"
    integration.base_url = None
    
    # Mock auth configuration
    auth_config_obj = MagicMock()
    auth_config_obj.action.value = "auth"
    auth_config_obj.data = {"token": "test-token"}
    
    integration.configurations = [auth_config_obj]
    return integration


@pytest.fixture
def mock_publish_event():
    """Mock the activity logger's publish_event to prevent actual PubSub calls."""
    with patch('app.services.activity_logger.publish_event', new=AsyncMock()):
        yield


@pytest.fixture
def mock_state_manager():
    """Mock all state manager operations."""
    with patch.object(handlers.state_manager, 'get_state', new=AsyncMock(return_value=None)), \
         patch.object(handlers.state_manager, 'set_state', new=AsyncMock()), \
         patch.object(handlers.state_manager, 'delete_state', new=AsyncMock()):
        yield


@pytest.mark.asyncio
async def test_action_auth_success(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    config.token.get_secret_value.return_value = "test-token"
    
    with patch.object(client, 'get_authentication_token', new=AsyncMock(return_value='session-token-123')):
        result = await handlers.action_auth(mock_integration, config)
        assert result == {"valid_credentials": True}


@pytest.mark.asyncio
async def test_action_auth_failure_returns_error(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    config.token.get_secret_value.return_value = "bad-token"
    
    with patch.object(client, 'get_authentication_token', new=AsyncMock(side_effect=client.WialonErrorException("Invalid token"))):
        result = await handlers.action_auth(mock_integration, config)
        assert result["valid_credentials"] is False
        assert "error" in result


@pytest.mark.asyncio
async def test_action_auth_http_error(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    config.token.get_secret_value.return_value = "test-token"

    with patch.object(client, 'get_authentication_token', new=AsyncMock(side_effect=httpx.HTTPError("Connection failed"))):
        result = await handlers.action_auth(mock_integration, config)
        assert result["valid_credentials"] is False
        assert "error" in result


@pytest.mark.asyncio
async def test_action_auth_invalid_token_reports_failure(mock_integration, mock_publish_event, mock_state_manager):
    """A token Wialon rejects must come back as valid_credentials=False with the
    reason, not blow up: the handler used to read a `.reason` attribute the
    exception does not have."""
    config = MagicMock()
    config.token.get_secret_value.return_value = "revoked-token"

    with patch.object(
        client, 'get_authentication_token',
        new=AsyncMock(side_effect=client.WialonInvalidAuthTokenException("Invalid authentication token. (reason=TOKEN_USER_NOT_FOUND)")),
    ):
        result = await handlers.action_auth(mock_integration, config)

    assert result["valid_credentials"] is False
    assert "TOKEN_USER_NOT_FOUND" in result["error"]


@pytest.mark.asyncio
async def test_action_fetch_samples_success(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    config.observations_to_extract = 2
    
    # Create mock vehicles
    vehicle1 = MagicMock()
    vehicle1.json.return_value = '{"id": 1}'
    vehicle2 = MagicMock()
    vehicle2.json.return_value = '{"id": 2}'
    vehicles = MagicMock(items=[vehicle1, vehicle2])
    
    # Mock the auth config
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', new=AsyncMock(return_value=vehicles)):
        result = await handlers.action_fetch_samples(mock_integration, config)
        assert result["observations_extracted"] == 2
        assert result["observations"] == [{"id": 1}, {"id": 2}]


@pytest.mark.asyncio
async def test_action_fetch_samples_wialon_error(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', 
                      new=AsyncMock(side_effect=client.WialonErrorException("API error"))):
        with pytest.raises(client.WialonErrorException):
            await handlers.action_fetch_samples(mock_integration, config)


@pytest.mark.asyncio
async def test_action_fetch_samples_http_error(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', 
                      new=AsyncMock(side_effect=httpx.HTTPError("Connection failed"))):
        with pytest.raises(httpx.HTTPError):
            await handlers.action_fetch_samples(mock_integration, config)


@pytest.mark.asyncio
async def test_action_pull_observations_success(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    
    # Create mock vehicle with position
    vehicle = MagicMock()
    vehicle.id = "dev1"
    vehicle.nm = "Device 1"
    pos = MagicMock()
    pos.dict.return_value = {
        "recorded_at": "2024-01-01 12:00:00+0000",
        "latitude": 1.0,
        "longitude": 2.0,
    }
    pos.t = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    vehicle.pos = pos
    vehicles = MagicMock(items=[vehicle])
    
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', new=AsyncMock(return_value=vehicles)), \
         patch.object(handlers, 'send_observations_to_gundi', new=AsyncMock(return_value={"status": "ok"})):
        result = await handlers.action_pull_observations(mock_integration, config)
        assert result["observations_extracted"] == 1
        assert result["details"] == {"status": "ok"}


@pytest.mark.asyncio
async def test_action_pull_observations_no_data(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    vehicles = MagicMock(items=[])
    
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', new=AsyncMock(return_value=vehicles)):
        result = await handlers.action_pull_observations(mock_integration, config)
        assert result["observations_extracted"] == 0
        assert "No transformed data" in result["details"]


@pytest.mark.asyncio
async def test_action_pull_observations_skips_devices_without_position(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    
    # Create mock vehicle without position
    vehicle = MagicMock()
    vehicle.id = "dev1"
    vehicle.nm = "Device 1"
    vehicle.pos = None
    vehicles = MagicMock(items=[vehicle])
    
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', new=AsyncMock(return_value=vehicles)), \
         patch.object(handlers, 'log_action_activity', new=AsyncMock()):
        result = await handlers.action_pull_observations(mock_integration, config)
        assert result["observations_extracted"] == 0
        assert "No transformed data" in result["details"]


@pytest.mark.asyncio
async def test_action_pull_observations_wialon_error(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()
    
    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'
    
    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh', 
                      new=AsyncMock(side_effect=client.WialonErrorException("API error"))):
        result = await handlers.action_pull_observations(mock_integration, config)
        assert "error" in result
        assert "Wialon API returned error" in result["details"]


@pytest.mark.asyncio
async def test_action_pull_observations_http_error(mock_integration, mock_publish_event, mock_state_manager):
    config = MagicMock()

    mock_auth_config = MagicMock()
    mock_auth_config.token.get_secret_value.return_value = 'test-token'

    with patch.object(handlers, 'get_auth_config', return_value=mock_auth_config), \
         patch.object(handlers, '_get_positions_with_session_refresh',
                      new=AsyncMock(side_effect=httpx.HTTPError("Connection failed"))):
        result = await handlers.action_pull_observations(mock_integration, config)
        assert "error" in result
        assert "HTTP error" in result["details"]


def test_pull_observations_keeps_the_portal_schedule():
    """Registration sends a crontab only when the handler carries one; the
    refactor added a ten-minute schedule, which would silently change the
    cadence configured in the portal. Cadence changes get their own PR."""
    assert not hasattr(handlers.action_pull_observations, "crontab_schedule")


@pytest.mark.asyncio
async def test_action_pull_observations_retries_three_times_without_max_time(
    mock_integration, mock_publish_event, mock_state_manager
):
    """backoff checks elapsed time at the start of each attempt; with a 60 s
    call timeout, a max_time=60 refuses the third attempt before it starts,
    so max_tries=3 was really only two attempts for the read-timeout case the
    retry exists for. The decorators inside action_pull_observations must not
    carry a max_time, and three attempts must actually be allowed."""
    # RED: the source must not carry a max_time on either backoff wrapper.
    assert "max_time" not in inspect.getsource(handlers.action_pull_observations)

    config = MagicMock()
    valid_response = client.WialonResponse(items=[])

    fetch_mock = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            valid_response,
        ]
    )

    with patch.object(handlers, "_get_positions_with_session_refresh", new=fetch_mock), \
         patch("backoff._async.asyncio.sleep", new=AsyncMock()):
        result = await handlers.action_pull_observations(mock_integration, config)

    assert fetch_mock.await_count == 3
    assert result["observations_extracted"] == 0
    assert "error" not in result
