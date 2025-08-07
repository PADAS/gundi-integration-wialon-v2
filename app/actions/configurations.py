from pydantic import SecretStr, Field

from .core import PullActionConfiguration, AuthActionConfiguration, ExecutableActionMixin


class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin):
    token: SecretStr = Field(..., format="password")


class FetchSamplesConfig(PullActionConfiguration, ExecutableActionMixin):
    observations_to_extract: int = 20


class PullObservationsConfig(PullActionConfiguration):
    # We may include something here in the future
    pass
