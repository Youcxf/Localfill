import asyncio
import logging

from app.models import (
    AccountPnl,
    AccountPosition,
    AccountRisk,
    AccountState,
    FillPair,
    OpenPosition,
    PnlResponse,
    PositionsResponse,
)
from app.services.dispatcher import OPEN_ORDER
from app.services.fills import day_text
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)


# read only stuff for the dashboard: open positions and pnl
class Reports:
    def __init__(self, manager, resolver, risk=None, fills=None):
        self.manager = manager
        self.resolver = resolver
        # the firm's lock on each account, and the fills localfill has seen (for the fees)
        self.risk = risk
        self.fills = fills

    # every (connection, account id) we trade on
    def all_accounts(self):
        pairs = []
        for conn in self.manager.pick():
            if not conn.account_names:
                continue
            for account_id in conn.target_account_ids():
                pairs.append((conn, account_id))
        return pairs

    # ---------- positions ----------

    async def positions(self):
        batches = await asyncio.gather(*(self.positions_for(c, a) for c, a in self.all_accounts()))
        positions = [p for batch in batches for p in batch]
        names = sorted({p.contract_name for p in positions})
        return PositionsResponse(positions=positions, distinct_contracts=names)

    async def positions_for(self, conn, account_id):
        account_name = conn.account_names.get(account_id, str(account_id))
        found = []
        try:
            rows = await conn.request("GET", f"/position/list?accountId={account_id}") or []
            for row in rows:
                net_pos = row.get("netPos", 0)
                contract_id = row.get("contractId")
                if not net_pos or contract_id is None:
                    continue
                if row.get("accountId", account_id) != account_id:
                    continue
                contract_name = await self.resolver.name_for_id(conn, contract_id)
                found.append(OpenPosition(
                    connection=conn.label,
                    account_id=account_id,
                    account_name=account_name,
                    contract_id=contract_id,
                    contract_name=contract_name,
                    net_pos=net_pos,
                ))
        except TradovateError as exc:
            logger.warning("[%s] could not read positions of account %s: %s", conn.label, account_id, exc)
        return found

    # ---------- pnl ----------

    async def pnl(self):
        # cash balances come for all accounts of a login at once, so ask once per connection
        by_connection = {}
        for conn, account_id in self.all_accounts():
            by_connection.setdefault(conn.label, (conn, []))[1].append(account_id)

        async def for_connection(conn, account_ids):
            try:
                cash_rows = await conn.request("GET", "/cashBalance/list") or []
            except TradovateError as exc:
                logger.warning("[%s] could not read cash balance: %s", conn.label, exc)
                cash_rows = []
            return await asyncio.gather(*(self.pnl_for(conn, a, cash_rows) for a in account_ids))

        batches = await asyncio.gather(*(for_connection(c, ids) for c, ids in by_connection.values()))
        return PnlResponse(accounts=[a for batch in batches for a in batch])

    async def pnl_for(self, conn, account_id, cash_rows):
        account_name = conn.account_names.get(account_id, str(account_id))
        empty = AccountPnl(connection=conn.label, account_id=account_id, account_name=account_name)
        try:
            fills = await conn.request("GET", f"/fill/list?accountId={account_id}") or []
            pairs = await conn.request("GET", f"/fillPair/list?accountId={account_id}") or []
        except TradovateError as exc:
            logger.warning("[%s] could not read fills of account %s: %s", conn.label, account_id, exc)
            return empty

        # fill id -> time, so each pair can say when it happened
        fill_times = {f["id"]: f.get("timestamp") for f in fills if "id" in f}
        fill_pairs = []
        for pair in pairs:
            if pair.get("archived") or "buyPrice" not in pair or "sellPrice" not in pair:
                continue
            fill_pairs.append(FillPair(
                buy_price=pair["buyPrice"],
                sell_price=pair["sellPrice"],
                qty=pair["qty"],
                ts=fill_times.get(pair.get("sellFillId")) or fill_times.get(pair.get("buyFillId")),
            ))

        # tradovate's own realized pnl for today, in the account currency
        own_rows = [c for c in cash_rows if c.get("accountId") == account_id]
        latest = max(own_rows, key=lambda c: c.get("timestamp", ""), default=None)
        realized_pnl = None
        trade_date = None
        if latest:
            realized_pnl = latest.get("realizedPnL")
            day = latest.get("tradeDate")
            if day:
                trade_date = f"{day['year']:04d}-{day['month']:02d}-{day['day']:02d}"

        return AccountPnl(
            connection=conn.label,
            account_id=account_id,
            account_name=account_name,
            realized_pnl=realized_pnl,
            trade_date=trade_date,
            fill_pairs=fill_pairs,
        )

    # ---------- balances ----------

    # every target account with its real balance, positions and risk state.
    # every=True: every account that is not blocked
    async def accounts(self, every=False):
        conns = [conn for conn in self.manager.pick() if conn.account_names]
        batches = await asyncio.gather(*(self.accounts_of(conn, every) for conn in conns))
        return [state for batch in batches for state in batch]

    async def accounts_of(self, conn, every):
        targets = conn.target_account_ids()
        if every:
            blocked = conn.blocked_ids()
            ids = [i for i in conn.account_names if i not in blocked]
        else:
            ids = targets
        if not ids:
            return []
        # the cash list and the risk state come for every account of a login at once
        cash_rows, risk = await asyncio.gather(self.cash_rows(conn), self.risk_of(conn))
        return await asyncio.gather(*(self.account_state(conn, i, i in targets, cash_rows, risk) for i in ids))

    async def account_state(self, conn, account_id, is_target, cash_rows, risk):
        state = AccountState(
            connection=conn.label,
            account_id=account_id,
            name=conn.account_names.get(account_id, str(account_id)),
            active=conn.account_active.get(account_id, True),
            is_target=is_target,
            risk=risk.get(account_id) or AccountRisk(),
        )
        snapshot, positions, working = await asyncio.gather(
            self.snapshot(conn, account_id),
            self.account_positions(conn, account_id),
            self.working_orders(conn, account_id),
            return_exceptions=True,
        )
        problems = []

        # the latest cash row: the fallback for the snapshot, and the trade date
        own_rows = [c for c in cash_rows if c.get("accountId") == account_id]
        latest = max(own_rows, key=lambda c: c.get("timestamp", ""), default={})
        state.trade_date = day_text(latest.get("tradeDate"))
        if isinstance(snapshot, dict):
            state.cash_balance = snapshot.get("totalCashValue")
            state.realized_pnl_today = snapshot.get("realizedPnL")
            state.open_pnl = snapshot.get("openPnL")
            state.net_liq = snapshot.get("netLiq")
        else:
            problems.append(f"balance: {snapshot}")
        if state.cash_balance is None:
            state.cash_balance = latest.get("amount")
        if state.realized_pnl_today is None:
            state.realized_pnl_today = latest.get("realizedPnL")

        if isinstance(positions, list):
            state.positions = positions
        else:
            problems.append(f"positions: {positions}")
        if isinstance(working, int):
            state.working_orders = working
        else:
            problems.append(f"orders: {working}")
        if self.fills:
            state.fees_today = await self.fills.fees_on(conn.label, account_id, state.trade_date)
        state.error = "; ".join(problems) or None
        return state

    async def cash_rows(self, conn):
        try:
            rows = await conn.request("GET", "/cashBalance/list") or []
        except TradovateError as exc:
            logger.warning("[%s] could not read cash balance: %s", conn.label, exc)
            return []
        return rows if isinstance(rows, list) else []

    # read again every time: this is also how a lock or unlock gets noticed when nothing polls
    async def risk_of(self, conn):
        if not self.risk:
            return {}
        return await self.risk.refresh(conn)

    # tradovate's own numbers: total cash, realized and open p/l, net liquidation value
    async def snapshot(self, conn, account_id):
        data = await conn.request("POST", "/cashBalance/getcashbalancesnapshot", {"accountId": account_id})
        if not isinstance(data, dict) or data.get("errorText"):
            raise TradovateError((data or {}).get("errorText") or "no snapshot")
        return data

    async def account_positions(self, conn, account_id):
        rows = await conn.request("GET", f"/position/list?accountId={account_id}") or []
        found = []
        for row in rows:
            if not row.get("netPos") or row.get("contractId") is None or row.get("accountId", account_id) != account_id:
                continue
            found.append(AccountPosition(contract=await self.resolver.name_for_id(conn, row["contractId"]), net_pos=row["netPos"]))
        return found

    async def working_orders(self, conn, account_id):
        orders = await conn.request("GET", f"/order/deps?masterid={account_id}") or []
        return sum(1 for o in orders if o.get("ordStatus") in OPEN_ORDER and o.get("accountId", account_id) == account_id)
