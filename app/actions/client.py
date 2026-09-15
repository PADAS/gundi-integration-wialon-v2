"""
Wialon API client module.

This module contains pure API calls to the Wialon API.
All functions accept primitive parameters (strings, etc.) and return response data.
State management and Gundi-specific logic should be handled in handlers.py.
"""
import httpx
import json
import pydantic
import logging

from datetime import datetime, timezone
from typing import List, Optional


logger = logging.getLogger(__name__)

WIALON_BASE_URL = "https://hst-api.wialon.com/wialon/"


# Exceptions
class WialonErrorException(Exception):
    """Raised when Wialon API returns an error response."""
    pass


class WialonInvalidSessionException(WialonErrorException):
    """Raised when the Wialon session is invalid or expired."""
    pass


class WialonInvalidAuthTokenException(WialonErrorException):
    """Raised when the Wialon authentication token is invalid."""
    pass

# Pydantic models for Wialon API requests/responses
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
        if v is None:
            return None
        return datetime.fromtimestamp(v, timezone.utc)


class WialonDataResponse(pydantic.BaseModel):
    nm: str = pydantic.Field("", alias="device_name")
    id: int = pydantic.Field(0, alias="device_id")
    pos: WialonDataResponsePos = pydantic.Field(None, alias="device_last_position")

    class Config:
        allow_population_by_field_name = True


class WialonResponse(pydantic.BaseModel):
    items: List[WialonDataResponse]


async def get_authentication_token(
    base_url: Optional[str],
    token: str
) -> str:
    """
    Authenticate with Wialon API and return session ID (eid).
    
    This is a pure API call with no state management.
    
    Args:
        base_url: Wialon API base URL (uses default if None)
        token: Wialon API token for authentication
        
    Returns:
        Session ID (eid) string
        
    Raises:
        WialonErrorException: If Wialon returns an error response
        httpx.HTTPError: If HTTP request fails
    """
    token_endpoint = "ajax.html"
    data = {
        "params": json.dumps({"token": token, "fl": "4"})
    }
    url = f"{base_url or WIALON_BASE_URL}{token_endpoint}"

    async with httpx.AsyncClient(timeout=10) as session:
        response = await session.post(
            url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=data,
            params={"svc": "token/login"}
        )
        response.raise_for_status()

    json_response = response.json()

    if "error" in json_response:

        if json_response.get("error") == 8:
            raise WialonInvalidAuthTokenException(f"Invalid authentication token. (reason={json_response.get('reason')})")
        raise WialonErrorException(
            f"Error {json_response.get('reason', json_response.get('error'))} "
            f"occurred while fetching token"
        )

    return json_response.get("eid")


async def get_positions_list(
    base_url: Optional[str],
    session_id: str
) -> WialonResponse:
    """
    Fetch vehicle positions from Wialon API.
    
    This is a pure API call with no state management.
    
    Args:
        base_url: Wialon API base URL (uses default if None)
        session_id: Valid Wialon session ID (eid)
        
    Returns:
        WialonResponse containing list of vehicle positions
        
    Raises:
        WialonInvalidSessionException: If session is invalid (error code 1)
        WialonErrorException: If Wialon returns another error
        httpx.HTTPError: If HTTP request fails
    """
    devices_endpoint = "ajax.html?svc=core/search_items"

    params = WialonDataRequestParams(
        spec=WialonDataRequestParamsSpec().dict()
    ).dict(by_alias=True)

    request_data = {
        "params": json.dumps(params),
        "sid": session_id
    }

    url = f"{base_url or WIALON_BASE_URL}{devices_endpoint}"

    async with httpx.AsyncClient(timeout=10) as session:
        response = await session.post(
            url,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data=request_data
        )
        response.raise_for_status()

    response_json = response.json()

    # Check if session is invalid (Error: 1)
    if "error" in response_json:
        if response_json["error"] == 1:
            raise WialonInvalidSessionException("Invalid session.")
        raise WialonErrorException(
            f"Error {response_json['error']} occurred while fetching positions"
        )

    return WialonResponse.parse_obj({
        "items": response_json.get("items", [])
    })
