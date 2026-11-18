import logging
import time
from datetime import datetime, timezone

from app.models import AccountRisk
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)

# tradovate's admin actions that leave an account free to trade. any other one (liquidate only,
# liquidated by auto liq...) counts as locked. check this list against what the firm really
# sends on its first lockout, see docs/api.md
OPEN_ACTIONS = {None, "", "Normal", "DisableAutoLiq", "PlaceAutoLiqOnHold", "RemoveAutoLiqFromHold"}


def in_future(text):
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when > datetime.now(timezone.utc)


# one row of /accountRiskStatus/list -> locked or not, and why
def read_risk(row):
    action = row.get("adminAction")
    liquidate_only = row.get("liquidateOnly")
    by_user = row.get("userTriggeredLiqOnly")
    reason = None
    if action not in OPEN_ACTIONS:
        reason = action
    elif by_user:
        reason = "liquidate only, turned on by the user"
    elif liquidate_only and in_future(liquidate_only):
        reason = f"liquidate only until {liquidate_only}"
    return AccountRisk(
        locked=reason is not None,
        reason=reason,
        admin_action=action,
        liquidate_only=str(liquidate_only) if liquidate_only else None,
        user_triggered_liq_only=by_user,
    )


# keeps the firm's risk state of every account (the daily loss limit lockout), read from tradovate.
# orders to a locked account are refused before anything is sent
class RiskWatch:
    def __init__(self, events):
        self.events = events
        # connection label -> {account id: AccountRisk}
        self.states = {}
        # connection label -> when it was read (monotonic)
        self.read_at = {}

    # reads it from tradovate again, and tells /events about accounts that got locked or unlocked
    async def refresh(self, conn):
        try:
            rows = await conn.request("GET", "/accountRiskStatus/list") or []
        except TradovateError as exc:
            logger.warning("[%s] could not read the account risk state: %s", conn.label, exc)
            # do not ask again for every order, an unknown state counts as not locked
            self.read_at[conn.label] = time.monotonic()
            return self.states.get(conn.label, {})

        found = {}
        for row in rows if isinstance(rows, list) else []:
            account_id = row.get("id", row.get("accountId"))
            if account_id is not None:
                found[account_id] = read_risk(row)

        old = self.states.get(conn.label)
        # the first read is not a change
        if old is not None:
            for account_id, risk in found.items():
                before = old.get(account_id)
                if risk.locked != (before.locked if before else False):
                    self.announce(conn, account_id, risk)
        self.states[conn.label] = found
        self.read_at[conn.label] = time.monotonic()
        return found

    def announce(self, conn, account_id, risk):
        name = conn.account_names.get(account_id, str(account_id))
        change = "locked" if risk.locked else "unlocked"
        logger.warning("[%s] account %s %s by the firm (%s)", conn.label, name, change, risk.reason or risk.admin_action)
        self.events.publish("accounts", {
            "connection": conn.label,
            "account_id": account_id,
            "name": name,
            "active": conn.account_active.get(account_id, True),
            "change": change,
            "reason": risk.reason,
        })

    # the states, read again when the last read is older than max_age seconds
    async def get(self, conn, max_age):
        read_at = self.read_at.get(conn.label)
        if read_at is not None and time.monotonic() - read_at < max_age:
            return self.states.get(conn.label, {})
        return await self.refresh(conn)

    # what we know without asking tradovate
    def known(self, conn, account_id):
        return self.states.get(conn.label, {}).get(account_id) or AccountRisk()
