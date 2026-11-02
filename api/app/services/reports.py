import asyncio
import logging

from app.models import AccountPnl, FillPair, OpenPosition, PnlResponse, PositionsResponse
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)


# read only stuff for the dashboard: open positions and pnl
class Reports:
    def __init__(self, manager, resolver):
        self.manager = manager
        self.resolver = resolver

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
