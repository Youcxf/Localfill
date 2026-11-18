import re
from datetime import time

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# "MNQ=48, NQ=3" -> {"MNQ": 48, "NQ": 3}
def parse_limits(text):
    limits = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"([A-Za-z0-9]+)\s*=\s*(\d+)", part)
        if not match:
            raise ValueError(f"'{part}' should look like MNQ=48")
        limits[match.group(1).upper()] = int(match.group(2))
    return limits


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
    # how often to try logging in again on connections that are offline (0 = never)
    reconnect_seconds: float = 60.0
    # how often to reload the account list of online connections, to notice closed accounts (0 = never)
    account_refresh_seconds: float = 600.0
    # the most contracts one alert may ask for per account (qty, or the size of position).
    # stops a typo or a leaked webhook url from sending a huge order. 0 = no limit
    max_contracts: int = 10
    # the same limit for single products, like "MNQ=48,NQ=3". a product not listed uses max_contracts
    max_contracts_per_symbol: str = ""

    # how often to ask tradovate for new fills, so take profits, stops and liquidations show up (0 = off)
    fill_poll_seconds: float = 1.0
    # how often the account risk state (the daily loss lockout) is read again while fills are polled
    risk_poll_seconds: float = 10.0
    # flattens every target account monday to friday at this new york time, like "16:44:30" (empty = off)
    daily_flatten_et: str = ""

    @field_validator("max_contracts_per_symbol")
    @classmethod
    def check_limits(cls, value):
        parse_limits(value)
        return value

    @field_validator("daily_flatten_et")
    @classmethod
    def check_flatten_time(cls, value):
        if value.strip():
            time.fromisoformat(value.strip())
        return value.strip()

    # max_contracts_per_symbol as a dict
    @property
    def symbol_limits(self):
        return parse_limits(self.max_contracts_per_symbol)
