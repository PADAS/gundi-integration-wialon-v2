import datetime
import httpx
import json
import logging
import stamina
import app.actions.client as client

from app.actions.configurations import AuthenticateConfig, FetchSamplesConfig, PullObservationsConfig
from app.services.activity_logger import activity_logger
from app.services.gundi import send_observations_to_gundi
from app.services.state import IntegrationStateManager


logger = logging.getLogger(__name__)


state_manager = IntegrationStateManager()


async def filter_and_transform(devices, integration_id, action_id):
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
    for device in devices:
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
                logger.info(
                    f"Excluding device ID '{device.id}' obs '{device.pos.t}'"
                )
                continue

        transformed_data.append(transform(device))

    return transformed_data


async def action_auth(integration, action_config: AuthenticateConfig):
    logger.info(f"Executing auth action with integration {integration} and action_config {action_config}...")
    try:
        eid = await client.get_authentication_token(
            integration=integration,
            config=action_config
        )
    except httpx.HTTPError as e:
        message = f"auth action returned error."
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        raise e
    else:
        logger.info(f"Authenticated with success. eid: {eid}")
        return {"valid_credentials": eid is not None}


async def action_fetch_samples(integration, action_config: FetchSamplesConfig):
    logger.info(f"Executing fetch_samples action with integration {integration} and action_config {action_config}...")
    try:
        config = client.get_fetch_samples_config(integration)
        vehicles = await client.get_positions_list(
            integration=integration,
            config=action_config
        )
    except httpx.HTTPError as e:
        message = f"fetch_samples action returned error."
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        raise e
    else:
        logger.info(f"Observations pulled with success.")
        return {
            "observations_extracted": config.observations_to_extract,
            "observations": [json.loads(vehicle.json()) for vehicle in vehicles.items][:config.observations_to_extract]
        }


@activity_logger()
async def action_pull_observations(integration, action_config: PullObservationsConfig):
    logger.info(f"Executing pull_observations action with integration {integration} and action_config {action_config}...")
    try:
        async for attempt in stamina.retry_context(
                on=httpx.HTTPError,
                attempts=3,
                wait_initial=datetime.timedelta(seconds=10),
                wait_max=datetime.timedelta(seconds=10),
        ):
            with attempt:
                vehicles = await client.get_positions_list(
                    integration=integration,
                    config=action_config
                )

        logger.info(f"Observations pulled with success.")

        transformed_data = await filter_and_transform(
            vehicles.items,
            str(integration.id),
            "pull_observations"
        )

        if transformed_data:
            async for attempt in stamina.retry_context(
                    on=httpx.HTTPError,
                    attempts=3,
                    wait_initial=datetime.timedelta(seconds=10),
                    wait_max=datetime.timedelta(seconds=10),
            ):
                with attempt:
                    try:
                        response = await send_observations_to_gundi(
                            observations=transformed_data,
                            integration_id=str(integration.id)
                        )
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
                        return [msg]
                    else:
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

        else:
            response = []
    except httpx.HTTPError as e:
        message = f"pull_observations action returned error."
        logger.exception(message, extra={
            "integration_id": str(integration.id),
            "attention_needed": True
        })
        raise e
    else:
        return response
