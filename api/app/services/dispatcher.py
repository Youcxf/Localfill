import asyncio
import logging
import time

from app.models import AccountResult, SignalResult
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)

# tradovate accepts an order first and can still reject it a moment later
REJECTED = ("Rejected", "Canceled", "Cancelled")
# orders that can still fill, flatten cancels these
OPEN_ORDER = ("Working", "Suspended", "PendingNew", "PendingReplace")


# MNQZ6 -> MNQ (tradovate names end with a month letter and one year digit)
def product_of(contract_name):
    return contract_name[:-2] if len(contract_name) > 2 else contract_name


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
        too_big = self.size_problem(signal)
        if too_big:
            return self.summarize(signal, signal.contract, [], [too_big])
        if signal.action == "flatten":
            return await self.flatten(signal)
        return await self.place_orders(signal)

    # refuses alerts asking for more than max_contracts per account
    def size_problem(self, signal):
        limit = self.manager.settings.max_contracts
        if limit <= 0 or signal.action == "flatten":
            return None
        size = abs(signal.position) if signal.position is not None else signal.qty
        if size > limit:
            return f"asks for {size} contracts, the limit is {limit} (LOCALFILL_MAX_CONTRACTS), nothing was sent"
        return None

    # the accounts of one connection this signal goes to: its target accounts, narrowed down
    # to signal.accounts when the alert names some
    def accounts_for(self, conn, signal):
        targets = conn.target_account_ids()
        if not signal.accounts:
            return targets
        wanted = {conn.account_ids.get(key) for key in signal.accounts}
        return [account_id for account_id in targets if account_id in wanted]

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
        contract_id = None
        if signal.position is not None:
            contract_id = await self.resolver.resolve_id(conns[0], contract)

        tasks = []
        for conn in conns:
            for account_id in self.accounts_for(conn, signal):
                if signal.position is None:
                    tasks.append(self.place_one(conn, account_id, signal, contract))
                else:
                    tasks.append(self.move_to_position(conn, account_id, signal, contract, contract_id))
        results = await asyncio.gather(*tasks)
        return self.summarize(signal, contract, results, warnings)

    # position mode: look at what the account really holds and only send the difference.
    # this also fixes accounts that missed a signal, got a partial fill or a double order
    async def move_to_position(self, conn, account_id, signal, contract, contract_id):
        account_name = conn.account_names.get(account_id, str(account_id))
        start = time.monotonic()
        try:
            positions = await self.open_positions(conn, account_id)
            current = sum(p.get("netPos", 0) for p in positions if p.get("contractId") == contract_id)
            # after a rollover the account can still hold last month's contract. trading the new
            # one on top of it would double the position, so stop and say so
            old = await self.other_months(conn, positions, contract, contract_id)
        except TradovateError as exc:
            # without the real position we can not know what to send, so send nothing
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=False,
                latency_ms=elapsed_ms(start),
                error=f"could not read the position, nothing sent: {exc}",
            )

        if old:
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=False,
                latency_ms=elapsed_ms(start),
                error=f"still holds {', '.join(old)}, roll it or flatten it first. nothing sent",
            )

        difference = signal.position - current
        if difference == 0:
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=True,
                latency_ms=elapsed_ms(start),
                status=f"already at {signal.position}",
            )
        # one order is enough even for a flip (long 2 to short 1 is a sell of 3)
        action = "buy" if difference > 0 else "sell"
        logger.info("[%s] account %s at %s, target %s: %s %s", conn.label, account_name, current, signal.position, action, abs(difference))
        result = await self.place_one(conn, account_id, signal, contract, action=action, qty=abs(difference))
        if result.success and result.status in ("Filled", "Completed"):
            # tradovate's position list can lag behind the fill. the next signal reads it, so we
            # wait here until it shows the new position, or it would trade the same thing again
            if not await self.wait_for_position(conn, account_id, contract_id, signal.position):
                result.status = f"{result.status}, position not confirmed yet"
                logger.warning("[%s] account %s: position does not show %s yet", conn.label, account_name, signal.position)
        return result

    # open positions of one account (the filter is in case tradovate returns other accounts too)
    async def open_positions(self, conn, account_id):
        positions = await conn.request("GET", f"/position/list?accountId={account_id}") or []
        return [p for p in positions if p.get("netPos", 0) != 0 and p.get("accountId", account_id) == account_id]

    # names of open positions in the same product as contract but another month
    async def other_months(self, conn, positions, contract, contract_id):
        found = []
        for position in positions:
            if position.get("contractId") == contract_id:
                continue
            name = await self.resolver.name_for_id(conn, position.get("contractId"))
            if product_of(name) == product_of(contract):
                found.append(f"{name} ({position.get('netPos')})")
        return found

    async def wait_for_position(self, conn, account_id, contract_id, target, tries=12, delay=0.25):
        for _ in range(tries):
            try:
                positions = await self.open_positions(conn, account_id)
            except TradovateError:
                return False
            if sum(p.get("netPos", 0) for p in positions if p.get("contractId") == contract_id) == target:
                return True
            await asyncio.sleep(delay)
        return False

    async def place_one(self, conn, account_id, signal, contract, action=None, qty=None):
        account_name = conn.account_names.get(account_id, str(account_id))
        payload = {
            "accountId": account_id,
            "action": (action or signal.action).capitalize(),
            "symbol": contract,
            "orderQty": qty or signal.qty,
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
            # never resent: a retry after a lost answer could place the order twice
            data = await conn.request("POST", "/order/placeorder", payload, retry=False)
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
            # only this order's fills. the whole day's fill list gets slow on busy days
            fills = await conn.request("GET", f"/fill/deps?masterid={order_id}") or []
        except TradovateError as exc:
            if getattr(exc, "status", None) != 404:
                return None
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
            for account_id in self.accounts_for(conn, signal):
                tasks.append(self.flatten_one(conn, account_id, contract_id))
        results = await asyncio.gather(*tasks)
        return self.summarize(signal, name, results, warnings)

    # cancels the account's orders that can still fill (only for one contract if given).
    # returns how many were cancelled, problems go into errors
    async def cancel_open_orders(self, conn, account_id, contract_id, errors):
        try:
            orders = await conn.request("GET", f"/order/deps?masterid={account_id}") or []
        except TradovateError as exc:
            errors.append(f"could not list working orders: {exc}")
            return 0
        cancelled = 0
        for order in orders:
            if order.get("ordStatus") not in OPEN_ORDER:
                continue
            if order.get("accountId", account_id) != account_id:
                continue
            if contract_id is not None and order.get("contractId") != contract_id:
                continue
            try:
                answer = await conn.request("POST", "/order/cancelorder", {"orderId": order["id"]})
            except TradovateError as exc:
                errors.append(f"could not cancel order {order.get('id')}: {exc}")
                continue
            failure = failure_of(answer)
            if failure:
                errors.append(f"could not cancel order {order.get('id')}: {failure}")
            else:
                cancelled += 1
        return cancelled

    async def flatten_one(self, conn, account_id, contract_id):
        account_name = conn.account_names.get(account_id, str(account_id))
        start = time.monotonic()
        try:
            errors = []
            # cancel working orders first, or a resting limit or stop could open a new position
            # right after we closed it
            cancelled = await self.cancel_open_orders(conn, account_id, contract_id, errors)

            open_positions = await self.open_positions(conn, account_id)
            if contract_id is not None:
                open_positions = [p for p in open_positions if p.get("contractId") == contract_id]

            if not open_positions:
                return AccountResult(
                    connection=conn.label,
                    account_id=account_id,
                    account_name=account_name,
                    success=not errors,
                    latency_ms=elapsed_ms(start),
                    status=f"cancelled {cancelled} order(s), nothing to close" if cancelled else "nothing to close",
                    error="; ".join(errors) or None,
                )

            last_response = None
            for position in open_positions:
                # tradovate wants "admin" here even though most examples leave it out
                last_response = await conn.request(
                    "POST",
                    "/order/liquidateposition",
                    {"accountId": account_id, "contractId": position.get("contractId"), "admin": False},
                    retry=False,
                )
                failure = failure_of(last_response)
                if failure:
                    errors.append(failure)

            closed_text = f"closed {len(open_positions)} position(s)"
            if cancelled:
                closed_text += f", cancelled {cancelled} order(s)"
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=not errors,
                latency_ms=elapsed_ms(start),
                status=None if errors else closed_text,
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
