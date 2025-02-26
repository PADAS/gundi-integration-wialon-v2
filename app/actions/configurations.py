from pydantic import SecretStr

from .core import PullActionConfiguration, AuthActionConfiguration, ExecutableActionMixin


class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin):
    token: SecretStr


class FetchSamplesConfig(PullActionConfiguration, ExecutableActionMixin):
    observations_to_extract: int = 20


class PullObservationsConfig(PullActionConfiguration):
    # We may include something here in the future
    pass
