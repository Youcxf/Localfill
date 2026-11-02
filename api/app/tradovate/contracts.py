import logging
import time

logger = logging.getLogger(__name__)


# turns a root symbol like MNQ into the front month ticker like MNQZ6
# the answer is cached because the front month only changes a few times a year
class ContractResolver:
    def __init__(self, cache_seconds=3600.0):
        self.cache_seconds = cache_seconds
        # symbol -> (contract dict, time we looked it up)
        self.cache = {}
        # contract id -> name
        self.names = {}

    async def lookup(self, conn, symbol):
        cached = self.cache.get(symbol)
        if cached and time.monotonic() - cached[1] < self.cache_seconds:
            return cached[0]

        # tradovate wants the query as ?t= (not ?name=)
        suggestions = await conn.request("GET", f"/contract/suggest?t={symbol}")
        if not suggestions:
            raise ValueError(f"tradovate does not know the symbol '{symbol}'")

        contract = await self.pick(conn, symbol, suggestions)
        if not contract.get("name"):
            raise ValueError(f"bad contract answer for '{symbol}': {contract}")

        self.cache[symbol] = (contract, time.monotonic())
        if contract.get("id") is not None:
            self.names[contract["id"]] = contract["name"]
        logger.info("%s is %s (contract id %s)", symbol, contract["name"], contract.get("id"))
        return contract

    # decides which of the suggested contracts to use
    async def pick(self, conn, symbol, suggestions):
        # the user typed a full ticker like MNQZ6, use exactly that one
        for item in suggestions:
            if item.get("name") == symbol:
                return item

        # suggest is not sorted by volume, so ask tradovate which maturity is the front month
        for item in suggestions:
            maturity_id = item.get("contractMaturityId")
            if maturity_id is None:
                continue
            try:
                maturity = await conn.request("GET", f"/contractMaturity/item?id={maturity_id}")
            except Exception:
                continue
            if isinstance(maturity, dict) and maturity.get("isFront"):
                return item

        # nothing is marked as front, fall back to the first suggestion
        return suggestions[0]

    # "MNQ" -> "MNQZ6"
    async def resolve(self, conn, symbol):
        contract = await self.lookup(conn, symbol)
        return contract["name"]

    # "MNQ" -> 123456 (some endpoints only take the id)
    async def resolve_id(self, conn, symbol):
        contract = await self.lookup(conn, symbol)
        if contract.get("id") is None:
            raise ValueError(f"no contract id for '{symbol}'")
        return contract["id"]

    # 123456 -> "MNQZ6". always asks tradovate for unknown ids, because a position can
    # sit in an old contract that is not the front month anymore
    async def name_for_id(self, conn, contract_id):
        if contract_id in self.names:
            return self.names[contract_id]
        item = await conn.request("GET", f"/contract/item?id={contract_id}")
        name = (item or {}).get("name") or f"contract {contract_id}"
        self.names[contract_id] = name
        return name
