import asyncio
import logging
import time

from app.models import AccountResult, SignalResult
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)

# tradovate accepts an order first and can still reject it a moment later
REJECTED = ("Rejected", "Canceled", "Cancelled")


def elapsed_ms(start):
    return round((time.monotonic() - start) * 1000, 1)


# tradovate answers with http 200 even when it refuses an order, the reason is in the body
def failure_of(data):
    if not isinstance(data, dict):
        return None
    reason = data.get("failureReason")
    if reason == "Success":
        return None
    return data.get("failureText") or reason


# sends signals to every account of every connection, all at the same time
class Dispatcher:
    def __init__(self, manager, resolver):
        self.manager = manager
        self.resolver = resolver

    async def run(self, signal):
        if signal.action == "flatten":
            return await self.flatten(signal)
        return await self.place_orders(signal)

    # the connections we can trade on, plus a warning for each one we have to skip
    def usable_connections(self, signal):
        conns = self.manager.pick(signal.connection)
        warnings = []
        if signal.connection and not conns:
            warnings.append(f"there is no connection called '{signal.connection}'")
        usable = []
        for conn in conns:
            if conn.account_names:
                usable.append(conn)
            else:
                warnings.append(f"connection '{conn.label}' has no accounts loaded (not logged in?), skipped")
        return usable, warnings

    def summarize(self, signal, contract, results, warnings):
        if not results:
            warnings.append("no target accounts, nothing was sent")
        succeeded = sum(1 for r in results if r.success)
        return SignalResult(
            action=signal.action,
            symbol=signal.symbol,
            contract=contract,
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=list(results),
            warnings=warnings,
        )

    # ---------- buy / sell ----------

    async def place_orders(self, signal):
        conns, warnings = self.usable_connections(signal)
        if not conns:
            return self.summarize(signal, signal.contract, [], warnings)

        # use the exact contract if we got one, otherwise find the front month
        contract = signal.contract or await self.resolver.resolve(conns[0], signal.symbol)

        tasks = []
        for conn in conns:
            for account_id in conn.target_account_ids():
                tasks.append(self.place_one(conn, account_id, signal, contract))
        results = await asyncio.gather(*tasks)
        return self.summarize(signal, contract, results, warnings)

    async def place_one(self, conn, account_id, signal, contract):
        account_name = conn.account_names.get(account_id, str(account_id))
        payload = {
            "accountId": account_id,
            "action": signal.action.capitalize(),
            "symbol": contract,
            "orderQty": signal.qty,
            "orderType": signal.order_type.capitalize(),
            "isAutomated": signal.is_automated,
        }
        # limit orders use price, stop orders use stopPrice
        if signal.order_type == "limit":
            payload["price"] = signal.price
        if signal.order_type == "stop":
            payload["stopPrice"] = signal.price

        start = time.monotonic()
        try:
            data = await conn.request("POST", "/order/placeorder", payload)
            order_id = data.get("orderId") if isinstance(data, dict) else None
            failure = failure_of(data)

            status = None
            fill_price = None
            if order_id and not failure:
                status = await self.wait_for_status(conn, order_id, signal.order_type == "market")
                # look for the fill even if the status is unclear, /order/item can be slower than /fill/list
                fill_price = await self.get_fill_price(conn, account_id, order_id)

            error = None
            if failure:
                error = failure
            elif order_id is None:
                error = "tradovate did not return an order id"
            elif status in REJECTED:
                error = f"order was {status}"

            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=error is None,
                latency_ms=elapsed_ms(start),
                order_id=order_id,
                status=status,
                fill_price=fill_price,
                error=error,
                response=data if isinstance(data, dict) else None,
            )
        except Exception as exc:
            # one account failing must never hide what happened on the others
            logger.error("[%s] order failed on account %s: %s", conn.label, account_id, exc)
            payload = exc.payload if isinstance(getattr(exc, "payload", None), dict) else None
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=False,
                latency_ms=elapsed_ms(start),
                error=str(exc),
                response=payload,
            )

    # /order/placeorder only means "we got your order". this asks what really happened to it.
    # market orders: wait until filled. limit / stop orders: "Working" is enough
    async def wait_for_status(self, conn, order_id, is_market, tries=5, delay=0.4):
        done = REJECTED + ("Filled", "Completed")
        if not is_market:
            done = done + ("Working",)
        status = None
        for _ in range(tries):
            try:
                data = await conn.request("GET", f"/order/item?id={order_id}")
                status = data.get("ordStatus") if isinstance(data, dict) else None
            except TradovateError:
                break
            if status in done:
                break
            await asyncio.sleep(delay)
        return status

    # average price of all fills of one order (the real price we got)
    async def get_fill_price(self, conn, account_id, order_id):
        try:
            fills = await conn.request("GET", f"/fill/list?accountId={account_id}") or []
        except TradovateError:
            return None
        ours = [f for f in fills if f.get("orderId") == order_id]
        total_qty = sum(f.get("qty", 0) for f in ours)
        if not total_qty:
            return None
        return sum(f["price"] * f["qty"] for f in ours) / total_qty

    # ---------- flatten ----------

    async def flatten(self, signal):
        conns, warnings = self.usable_connections(signal)

        # only close one contract if a symbol was given, otherwise close everything
        name = signal.contract or signal.symbol
        contract_id = None
        if name and conns:
            contract_id = await self.resolver.resolve_id(conns[0], name)

        tasks = []
        for conn in conns:
            for account_id in conn.target_account_ids():
                tasks.append(self.flatten_one(conn, account_id, contract_id))
        results = await asyncio.gather(*tasks)
        return self.summarize(signal, name, results, warnings)

    async def flatten_one(self, conn, account_id, contract_id):
        account_name = conn.account_names.get(account_id, str(account_id))
        start = time.monotonic()
        try:
            positions = await conn.request("GET", f"/position/list?accountId={account_id}") or []
            # keep only this account's open positions (the filter is just to be safe)
            open_positions = [
                p for p in positions
                if p.get("netPos", 0) != 0 and p.get("accountId", account_id) == account_id
            ]
            if contract_id is not None:
                open_positions = [p for p in open_positions if p.get("contractId") == contract_id]

            if not open_positions:
                return AccountResult(
                    connection=conn.label,
                    account_id=account_id,
                    account_name=account_name,
                    success=True,
                    latency_ms=elapsed_ms(start),
                    status="nothing to close",
                )

            errors = []
            last_response = None
            for position in open_positions:
                # tradovate wants "admin" here even though most examples leave it out
                last_response = await conn.request(
                    "POST",
                    "/order/liquidateposition",
                    {"accountId": account_id, "contractId": position.get("contractId"), "admin": False},
                )
                failure = failure_of(last_response)
                if failure:
                    errors.append(failure)

            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=not errors,
                latency_ms=elapsed_ms(start),
                status=f"closed {len(open_positions)} position(s)" if not errors else None,
                error="; ".join(errors) or None,
                response=last_response if isinstance(last_response, dict) else None,
            )
        except Exception as exc:
            logger.error("[%s] flatten failed on account %s: %s", conn.label, account_id, exc)
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=False,
                latency_ms=elapsed_ms(start),
                error=str(exc),
            )
