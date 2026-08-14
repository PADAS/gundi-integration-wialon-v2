import datetime
import httpx
import json
import logging

import backoff
import app.actions.client as client

from typing import Optional

from app.actions.configurations import AuthenticateConfig, FetchSamplesConfig, PullObservationsConfig
from app.services.activity_logger import activity_logger, log_action_activity
from app.services.action_scheduler import crontab_schedule
from app.services.errors import ConfigurationNotFound
from app.services.gundi import send_observations_to_gundi
from app.services.state import IntegrationStateManager
from app.services.utils import find_config_for_action
from gundi_core.events.integrations import LogLevel


logger = logging.getLogger(__name__)


state_manager = IntegrationStateManager()


# Session management helpers
async def _get_cached_session(integration_id: str) -> Optional[str]:
    """Get cached session ID from state."""
    cached = await state_manager.get_state(integration_id, "session")
    return cached.get("eid") if cached else None


async def _save_session(integration_id: str, session_id: str):
    """Save session ID to state."""
    await state_manager.set_state(integration_id, "session", {"eid": session_id})


async def _clear_session(integration_id: str):
    """Clear cached session from state."""
    await state_manager.delete_state(integration_id, "session")


def get_auth_config(integration) -> str:
    """
    Get the authentication token from the integration's auth configuration.
    
    Args:
        integration: Gundi integration object
        
    Returns:
        The API token string
        
    Raises:
        ConfigurationNotFound: If auth configuration is missing
    """
    auth_config = find_config_for_action(integration.configurations, "auth")
    if not auth_config:
        raise ConfigurationNotFound(
            f"Authentication settings for integration {str(integration.id)} "
            f"are missing. Please fix the integration setup in the portal."
        )
    parsed_config = AuthenticateConfig.parse_obj(auth_config.data)
    return parsed_config


async def _get_or_create_session(
    integration_id: str,
    base_url: Optional[str],
    token: str
) -> str:
    """
    Get cached session ID or authenticate to create a new one.
    
    Args:
        integration_id: UUID of the integration
        base_url: Wialon API base URL
        token: Wialon API token
        
    Returns:
        Valid session ID (eid)
    """
    # Try to get cached session
    session_id = await _get_cached_session(integration_id)
    if session_id:
        return session_id
    
    # No cached session, authenticate to get a new one
    session_id = await client.get_authentication_token(base_url, token)
    await _save_session(integration_id, session_id)
    return session_id


async def _on_invalid_session(details):
    """Clear cached session when it becomes invalid."""
    integration_id = details['args'][0]
    logger.warning(
        f"Invalid session for integration {integration_id}, "
        f"clearing cache and retrying (attempt {details['tries']}/3)"
    )
    await _clear_session(integration_id)


@backoff.on_exception(
    backoff.expo,
    client.WialonInvalidSessionException,
    max_tries=3,
    on_backoff=_on_invalid_session,
    jitter=backoff.full_jitter
)
async def _get_positions_with_session_refresh(
    integration_id: str,
    base_url: Optional[str],
    token: str
) -> client.WialonResponse:
    """
    Get positions list, handling session invalidation by refreshing the session.
    
    Args:
        integration_id: UUID of the integration
        base_url: Wialon API base URL
        token: Wialon API token
        
    Returns:
        WialonResponse containing vehicle positions
    """
    session_id = await _get_or_create_session(integration_id, base_url, token)
    return await client.get_positions_list(base_url, session_id)


async def filter_and_transform(devices, integration_id, action_id):
    """Transform Wialon device data to Gundi observation format."""
    def transform(device):
        device_id = device.id
        device_name = device.nm

        device_positions = device.pos.dict(by_alias=True)

        recorded_at = device_positions.pop("recorded_at")
        lat = device_positions.pop("latitude")
        lon = device_positions.pop("longitude")

        return {
            "source": device_id,
            "source_name": device_name,
            'type': 'tracking-device',
            "recorded_at": recorded_at,
            "location": {
                "lat": lat,
                "lon": lon
            },
            "additional": device_positions
        }

    transformed_data = []
    devices_without_position = []
    for device in devices:
        # Skip devices without position data
        if device.pos is None:
            logger.debug(f"Skipping device ID '{device.id}' - no position data available")
            devices_without_position.append({
                "device_id": device.id,
                "device_name": device.nm
            })
            continue

        # Get current state for the device
        current_state = await state_manager.get_state(
            integration_id,
            action_id,
            device.id
        )

        if current_state:
            # Compare current state with new data
            latest_device_timestamp = datetime.datetime.strptime(
                current_state.get("latest_device_timestamp"),
                '%Y-%m-%d %H:%M:%S%z'
            )

            if device.pos.t <= latest_device_timestamp:
                # Data is not new, not transform
                logger.debug(
                    f"Excluding device ID '{device.id}' obs '{device.pos.t}'"
                )
                continue

        transformed_data.append(transform(device))

    return transformed_data, devices_without_position


@activity_logger()
async def action_auth(integration, action_config: AuthenticateConfig):
    """
    Authenticate with Wialon API to validate credentials.
    """
    logger.info(f"Executing auth action for integration {integration.id}...")
    try:
        eid = await client.get_authentication_token(
            base_url=integration.base_url,
            token=action_config.token.get_secret_value()
        )
        # Cache the session for future use
        await _save_session(str(integration.id), eid)
    except client.WialonInvalidAuthTokenException as e:
        message = f"Invalid authentication token. (reason={e.reason})"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        return {"valid_credentials": False, "error": message}
    except client.WialonErrorException as e:
        message = f"auth action returned Wialon error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        return {"valid_credentials": False, "error": str(e)}
    except httpx.HTTPError as e:
        message = f"auth action returned HTTP error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        return {"valid_credentials": False, "error": str(e)}
    else:
        logger.info(f"Authenticated with success. eid: {eid}")
        return {"valid_credentials": eid is not None}


@activity_logger()
async def action_fetch_samples(integration, action_config: FetchSamplesConfig):
    """
    Fetch sample observations from Wialon API for testing/preview.
    """
    logger.info(f"Executing fetch_samples action for integration {integration.id}...")
    try:
        # Get auth token from integration config
        auth_config = get_auth_config(integration)
        token = auth_config.token.get_secret_value()
        # Get positions with automatic session management
        vehicles = await _get_positions_with_session_refresh(
            str(integration.id),
            integration.base_url,
            token
        )
    except (client.WialonErrorException, client.WialonInvalidSessionException) as e:
        message = f"fetch_samples action returned Wialon error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        raise
    except httpx.HTTPError as e:
        message = f"fetch_samples action returned HTTP error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        raise
    else:
        logger.info(f"Observations pulled with success.")
        observations = [
            json.loads(vehicle.json()) 
            for vehicle in vehicles.items
        ][:action_config.observations_to_extract]
        return {
            "observations_extracted": len(observations),
            "observations": observations
        }


@activity_logger()
@crontab_schedule("*/10 * * * *")
async def action_pull_observations(integration, action_config: PullObservationsConfig):
    """
    Pull observations from Wialon API and send to Gundi.
    """
    logger.info(f"Executing pull_observations action for integration {integration.id}...")
    result = {"observations_extracted": 0, "details": {}}
    
    try:
        # Get auth token from integration config
        auth_config = get_auth_config(integration)
        token = auth_config.token.get_secret_value()
        
        # Get positions with automatic session management and retry
        @backoff.on_exception(
            backoff.expo,
            httpx.HTTPError,
            max_tries=3,
            max_time=60,
            jitter=backoff.full_jitter
        )
        async def fetch_positions():
            return await _get_positions_with_session_refresh(
                str(integration.id),
                integration.base_url,
                token
            )
        
        vehicles = await fetch_positions()
        logger.info(f"Observations pulled with success.")

        transformed_data, devices_without_position = await filter_and_transform(
            vehicles.items,
            str(integration.id),
            "pull_observations"
        )

        # Log activity if there are devices without position data
        if devices_without_position:
            device_names = [d["device_name"] for d in devices_without_position[:3]]
            names_str = ", ".join(device_names)
            if len(devices_without_position) > 3:
                names_str += f", and {len(devices_without_position) - 3} more"
            await log_action_activity(
                integration_id=str(integration.id),
                action_id="pull_observations",
                title=f"Skipped {len(devices_without_position)} device(s) without position data: {names_str}",
                level=LogLevel.INFO,
                data={"devices_without_position": devices_without_position}
            )

        total_observations = 0
        if transformed_data:
            @backoff.on_exception(
                backoff.expo,
                httpx.HTTPError,
                max_tries=3,
                max_time=60,
                jitter=backoff.full_jitter
            )
            async def send_to_gundi():
                return await send_observations_to_gundi(
                    observations=transformed_data,
                    integration_id=str(integration.id)
                )
            
            try:
                response = await send_to_gundi()
            except httpx.HTTPError as e:
                msg = f'Sensors API returned error for integration_id: {str(integration.id)}. Exception: {e}'
                logger.exception(
                    msg,
                    extra={
                        'needs_attention': True,
                        'integration_id': str(integration.id),
                        'action_id': "pull_observations"
                    }
                )
                result["message"] = msg
                return result
            else:
                total_observations += len(transformed_data)
                for vehicle in transformed_data:
                    # Update state
                    state = {
                        "latest_device_timestamp": vehicle.get("recorded_at")
                    }
                    await state_manager.set_state(
                        str(integration.id),
                        "pull_observations",
                        state,
                        vehicle.get("source")
                    )
                result["observations_extracted"] = total_observations
                result["details"] = response
        else:
            result["details"] = "No transformed data to send."
    except client.WialonInvalidAuthTokenException as e:
        message = f"The Authentication Token is invalid. This will require getting a new token from Wialon."
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        result["details"] = message
        result["error"] = str(e)

        await log_action_activity(
            integration_id=str(integration.id),
            action_id=action_pull_observations.__name__.replace('action_', ''),
            title=message,
            level=LogLevel.ERROR,
            data={"error": str(e)}
        )
        return result

    except (client.WialonErrorException, client.WialonInvalidSessionException) as e:
        message = f"Wialon API returned error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        result["details"] = message
        result["error"] = str(e)
        return result
    except httpx.HTTPError as e:
        message = f"pull_observations action returned HTTP error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        result["details"] = message
        result["error"] = str(e)
        return result
    except ConfigurationNotFound as e:
        message = f"Configuration error: {str(e)}"
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        result["details"] = message
        result["error"] = str(e)
        return result
    else:
        return result
