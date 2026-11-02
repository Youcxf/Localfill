from pydantic_settings import BaseSettings, SettingsConfigDict


# every setting can be changed with an env var, prefixed with LOCALFILL_
# example: LOCALFILL_INGRESS_PORT=9000
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOCALFILL_", extra="ignore")

    # where the database and the encryption key live
    data_dir: str = "data"

    # two servers: ingress is the one you expose to tradingview, admin is the dashboard + api
    # both listen on localhost by default, the docker image changes this to 0.0.0.0
    ingress_host: str = "127.0.0.1"
    ingress_port: int = 8010
    admin_host: str = "127.0.0.1"
    admin_port: int = 8011

    log_level: str = "INFO"

    # the public url of your tunnel, only used to show the full webhook url in the dashboard
    public_url: str = ""

    # folder with the built angular app, served by the admin server if it exists
    static_dir: str = "static"

    # ignore an identical signal that shows up again within this many seconds (0 = off)
    dedupe_seconds: float = 2.0

    # tradovate
    tradovate_demo_url: str = "https://demo.tradovateapi.com/v1"
    tradovate_live_url: str = "https://live.tradovateapi.com/v1"
    # tradovate's web client version, bump it if logins start failing after an update
    tradovate_app_version: str = "3.261030.0"
    http_timeout_seconds: float = 10.0
    max_retries: int = 5
    retry_backoff_base: float = 0.5
    # renew the token after this fraction of its lifetime has passed
    token_renew_threshold: float = 0.75
    # how long we remember which contract is the front month
    contract_cache_seconds: float = 3600.0
