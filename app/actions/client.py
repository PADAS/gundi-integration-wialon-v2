import httpx
import json
import pydantic
import stamina
import logging

from datetime import datetime, timezone
from app.actions.configurations import (
    AuthenticateConfig,
    FetchSamplesConfig,
    PullObservationsConfig
)
from app.services.errors import ConfigurationNotFound
from app.services.utils import find_config_for_action
from app.services.state import IntegrationStateManager
from typing import List


logger = logging.getLogger(__name__)
state_manager = IntegrationStateManager()

WIALON_BASE_URL = "https://hst-api.wialon.com/wialon/"


# Exceptions
class WialonErrorException(Exception):
    pass


class WialonInvalidSessionException(Exception):
    pass


# Pydantic models (representing integration objects to receive/manipulate info from tle external API)
class WialonDataRequestParamsSpec(pydantic.BaseModel):
    itemsType: str = "avl_unit"
    propName: str = "sys_name, sys_id"
    propValueMask: str = "*"
    sortType: str = "sys_name"


class WialonDataRequestParams(pydantic.BaseModel):
    spec: dict = List[WialonDataRequestParamsSpec]
    force: int = 1
    flags: int = 1025
    f: int = pydantic.Field(0, alias="from")
    to: int = 0


class WialonDataResponsePos(pydantic.BaseModel):
    t: datetime = pydantic.Field(None, alias="recorded_at")
    f: int = pydantic.Field(0, alias="sensors_flags")
    y: float = pydantic.Field(0.0, alias="latitude")
    x: float = pydantic.Field(0.0, alias="longitude")
    c: int = pydantic.Field(0, alias="course")
    z: float = pydantic.Field(0.0, alias="altitude")
    s: int = pydantic.Field(0, alias="speed")
    sc: int = pydantic.Field(0, alias="satellites_count")

    class Config:
        allow_population_by_field_name = True

    @pydantic.validator('t', pre=True)
    def parse_datetime(cls, v):
        return datetime.fromtimestamp(v, timezone.utc)


class WialonDataResponse(pydantic.BaseModel):
    nm: str = pydantic.Field("", alias="device_name")
    id: int = pydantic.Field(0, alias="device_id")
    pos: WialonDataResponsePos = pydantic.Field(None, alias="device_last_position")

    class Config:
        allow_population_by_field_name = True


class WialonResponse(pydantic.BaseModel):
    items: List[WialonDataResponse]


def get_auth_config(integration):
    # Look for the login credentials, needed for any action
    auth_config = find_config_for_action(
        configurations=integration.configurations,
        action_id="auth"
    )
    if not auth_config:
        raise ConfigurationNotFound(
            f"Authentication settings for integration {str(integration.id)} "
            f"are missing. Please fix the integration setup in the portal."
        )
    return AuthenticateConfig.parse_obj(auth_config.data)


def get_fetch_samples_config(integration):
    # Look for the login credentials, needed for any action
    fetch_samples_config = find_config_for_action(
        configurations=integration.configurations,
        action_id="fetch_samples"
    )
    if not fetch_samples_config:
        raise ConfigurationNotFound(
            f"fetch_samples settings for integration {str(integration.id)} "
            f"are missing. Please fix the integration setup in the portal."
        )
    return FetchSamplesConfig.parse_obj(fetch_samples_config.data)


def get_pull_config(integration):
    # Look for the login credentials, needed for any action
    pull_config = find_config_for_action(
        configurations=integration.configurations,
        action_id="pull_observations"
    )
    if not pull_config:
        raise ConfigurationNotFound(
            f"pull_config settings for integration {str(integration.id)} "
            f"are missing. Please fix the integration setup in the portal."
        )
    return PullObservationsConfig.parse_obj(pull_config.data)


async def build_request_params(integration):
    """
        Call the client's 'ajax.html?svc=token/login' endpoint

    :return: The authentication token
    """
    saved_token = await state_manager.get_state(
        str(integration.id),
        "get_authentication_token"
    )

    if saved_token:
        token = saved_token["eid"]
    else:
        try:
            token = await get_authentication_token(integration, get_auth_config(integration))
        except WialonErrorException as e:
            logger.exception(f"Error fetching authentication token for integration {integration.id}: {str(e)}")
            raise

    params = WialonDataRequestParams(
        spec=WialonDataRequestParamsSpec().dict()
    ).dict(by_alias=True)

    return {
        "params": json.dumps(params),
        "sid": token
    }


async def get_authentication_token(integration, config):
    token_endpoint = "ajax.html?svc=token/login"

    data = {
        "params": json.dumps({"token": config.token.get_secret_value(), "fl": "4"})
    }

    url = f"{integration.base_url or WIALON_BASE_URL}{token_endpoint}"

    async with httpx.AsyncClient(timeout=120) as session:
        response = await session.post(
            url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=data
        )
        response.raise_for_status()

    json_response = response.json()

    if "error" in json_response:
        raise WialonErrorException(f"Error {json_response.get('reason', json_response.get('error'))} occurred while fetching token")

    state = {
        "eid": json_response.get("eid")
    }
    await state_manager.set_state(
        str(integration.id),
        "get_authentication_token",
        state
    )

    return json_response.get("eid")


@stamina.retry(on=WialonInvalidSessionException, attempts=3)
async def get_positions_list(integration):
    try:
        devices_endpoint = "ajax.html?svc=core/search_items"

        params = await build_request_params(integration)

        url = f"{integration.base_url or WIALON_BASE_URL}{devices_endpoint}"

        async with httpx.AsyncClient(timeout=120) as session:
            response = await session.post(
                url,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                data=params
            )
            response.raise_for_status()

        response_json = response.json()

        # Check is session is invalid (Error: 1)
        if "error" in response_json:
            if response_json["error"] == 1:
                await state_manager.delete_state(
                    str(integration.id),
                    "get_authentication_token"
                )
                raise WialonInvalidSessionException("Invalid session.")
            raise WialonErrorException(f"Error {response_json['error']} occurred while fetching positions")

        return WialonResponse.parse_obj({
            "items": response_json.get("items", [])
        })
    except WialonErrorException as e:
        logger.exception(f"WialonErrorException for integration {integration.id}: {str(e)}")
        raise
