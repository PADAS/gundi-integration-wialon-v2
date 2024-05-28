from .core import PullActionConfiguration, AuthActionConfiguration


class AuthenticateConfig(AuthActionConfiguration):
    token: str


class FetchSamplesConfig(PullActionConfiguration):
    observations_to_extract: int = 20


class PullObservationsConfig(PullActionConfiguration):
    # We may include something here in the future
    pass
