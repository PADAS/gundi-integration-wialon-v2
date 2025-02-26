import httpx
import json
import pydantic

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


state_manager = IntegrationStateManager()


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
    token = await get_authentication_token(integration, get_auth_config(integration))

    params = WialonDataRequestParams(
        spec=WialonDataRequestParamsSpec().dict()
    ).dict(by_alias=True)

    return {
        "params": json.dumps(params),
        "sid": token
    }


async def get_authentication_token(integration, config):
    current_state = await state_manager.get_state(
        str(integration.id),
        "get_authentication_token"
    )

    if current_state:
        return current_state["eid"]

    token_endpoint = "ajax.html?svc=token/login"

    data = {
        "params": json.dumps({"token": config.token.get_secret_value(), "fl": "4"})
    }

    url = f"{integration.base_url}{token_endpoint}"

    async with httpx.AsyncClient(timeout=120) as session:
        response = await session.post(
            url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=data
        )
        response.raise_for_status()

    json_response = response.json()

    state = {
        "eid": json_response.get("eid")
    }
    await state_manager.set_state(
        str(integration.id),
        "get_authentication_token",
        state
    )

    return json_response.get("eid")


async def get_positions_list(integration, config):
    devices_endpoint = "ajax.html?svc=core/search_items"

    params = await build_request_params(integration)

    url = f"{integration.base_url}{devices_endpoint}"

    async with httpx.AsyncClient(timeout=120) as session:
        response = await session.post(
            url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=params
        )
        response.raise_for_status()

    return WialonResponse.parse_obj({
        "items": response.json().get("items")
    })
