import asyncio
import json
import logging
import time

from app.models import AccountResult, SignalResult
from app.tradovate.client import TradovateError

logger = logging.getLogger(__name__)

# tradovate accepts an order first and can still reject it a moment later
REJECTED = ("Rejected", "Canceled", "Cancelled")
# orders that can still fill, flatten cancels these (a bracket waiting for its entry is "Suspended")
OPEN_ORDER = ("Working", "Suspended", "PendingNew", "PendingReplace")
# tradovate's id for the "multibracket" order strategy (from /orderStrategyType/list)
MULTIBRACKET = 2
# in /orderStrategyLink the entry order has this label, the brackets have "0", "1"...
ENTRY_LABEL = "256"


# rounds a price or a distance to the contract's tick size
def to_tick(value, tick):
    return round(round(value / tick) * tick, 10)


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
    def __init__(self, manager, resolver, brackets):
        self.manager = manager
        self.resolver = resolver
        self.brackets = brackets

    async def run(self, signal):
        plan = None
        if signal.uses_bracket:
            # the alert mixed with its preset. a missing preset or stop raises ValueError
            plan = await self.brackets.plan_for(signal)
        too_big = self.size_problem(signal, plan)
        if too_big:
            return self.summarize(signal, signal.contract, [], [too_big], plan)
        if signal.action == "flatten":
            return await self.flatten(signal)
        return await self.place_orders(signal, plan)

    # refuses alerts asking for more than max_contracts per account
    def size_problem(self, signal, plan=None):
        limit = self.manager.settings.max_contracts
        if limit <= 0 or signal.action == "flatten":
            return None
        if plan:
            size = plan.total
        elif signal.position is not None:
            size = abs(signal.position)
        else:
            size = signal.qty
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

    def summarize(self, signal, contract, results, warnings, plan=None):
        if not results:
            warnings.append("no target accounts, nothing was sent")
        succeeded = sum(1 for r in results if r.success)
        qty = None
        if plan:
            qty = plan.total
        elif signal.action != "flatten" and signal.position is None:
            qty = signal.qty
        return SignalResult(
            action=signal.action,
            symbol=signal.symbol,
            contract=contract,
            qty=qty,
            bracket=plan.describe() if plan else None,
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=list(results),
            warnings=warnings,
        )

    # ---------- buy / sell ----------

    async def place_orders(self, signal, plan=None):
        conns, warnings = self.usable_connections(signal)
        if not conns:
            return self.summarize(signal, signal.contract, [], warnings, plan)

        # use the exact contract if we got one, otherwise find the front month
        contract = signal.contract or await self.resolver.resolve(conns[0], signal.symbol)
        contract_id = await self.resolver.resolve_id(conns[0], contract)
        tick = await self.resolver.tick_size(conns[0], contract) if plan else None

        tasks = []
        for conn in conns:
            for account_id in self.accounts_for(conn, signal):
                if plan:
                    tasks.append(self.place_bracket(conn, account_id, signal, plan, contract, contract_id, tick))
                elif signal.position is None:
                    tasks.append(self.place_plain(conn, account_id, signal, contract, contract_id))
                else:
                    tasks.append(self.move_to_position(conn, account_id, signal, contract, contract_id))
        results = await asyncio.gather(*tasks)
        return self.summarize(signal, contract, results, warnings, plan)

    # a plain buy or sell. when a bracket localfill started is still open on this account, tradovate
    # keeps its take profits and stop working even after the position is closed, so:
    # an order the other way closes the position and its bracket instead (it is the strategy's exit),
    # and on a flat account the leftover bracket orders are cancelled before the order goes out
    async def place_plain(self, conn, account_id, signal, contract, contract_id):
        try:
            if await self.open_brackets(conn, account_id, contract_id):
                positions = await self.open_positions(conn, account_id)
                current = sum(p.get("netPos", 0) for p in positions if p.get("contractId") == contract_id)
                if current == 0:
                    await self.cancel_open_orders(conn, account_id, contract_id, [])
                elif (current > 0) != (signal.action == "buy"):
                    result = await self.flatten_one(conn, account_id, contract_id)
                    if result.success:
                        result.status = f"closed {current:g} with its take profits and stop, instead of {signal.action} {signal.qty}"
                    return result
        except TradovateError as exc:
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=conn.account_names.get(account_id, str(account_id)),
                success=False,
                latency_ms=0,
                error=f"could not check the open bracket, nothing sent: {exc}",
            )
        return await self.place_one(conn, account_id, signal, contract)

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
            # a bracket localfill started that is still open on this contract
            bracket_open = bool(await self.open_brackets(conn, account_id, contract_id))
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

        closed_text = None
        if bracket_open:
            if current == 0:
                # flat, but the old bracket's orders are still working and could open a position
                await self.cancel_open_orders(conn, account_id, contract_id, [])
            elif signal.position == 0 or (signal.position > 0) != (current > 0):
                # going flat or to the other side: close it with its take profits and stop first,
                # a plain order would leave them working
                closed = await self.flatten_one(conn, account_id, contract_id)
                if not closed.success or not await self.wait_for_position(conn, account_id, contract_id, 0):
                    closed.success = False
                    closed.error = closed.error or "closed the bracket but tradovate does not show it flat yet, nothing else sent"
                    return closed
                closed_text = f"closed {current:g} with its take profits and stop"
                if signal.position == 0:
                    closed.status = closed_text
                    return closed
                current = 0

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
        if closed_text and result.status:
            result.status = f"{closed_text}, then {result.status}"
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

    # ---------- brackets ----------

    # the entry with its take profits and stop, as one tradovate order strategy. tradovate places
    # the brackets right after the fill and moves the stops to breakeven itself, so nothing here
    # has to keep watching the trade
    async def place_bracket(self, conn, account_id, signal, plan, contract, contract_id, tick):
        account_name = conn.account_names.get(account_id, str(account_id))
        side = 1 if signal.action == "buy" else -1
        start = time.monotonic()

        def answer(success, status=None, error=None, **extra):
            return AccountResult(
                connection=conn.label,
                account_id=account_id,
                account_name=account_name,
                success=success,
                latency_ms=elapsed_ms(start),
                status=status,
                error=error,
                **extra,
            )

        try:
            notes = []
            # what the account already holds of this contract
            positions = await self.open_positions(conn, account_id)
            current = sum(p.get("netPos", 0) for p in positions if p.get("contractId") == contract_id)
            if current and plan.if_open == "skip":
                return answer(True, status=f"skipped, it already holds {current:g}")
            if current and (plan.if_open == "replace" or current * side < 0):
                closed = await self.flatten_one(conn, account_id, contract_id)
                if not closed.success:
                    return answer(False, error=f"could not close the {current:g} it holds, nothing sent: {closed.error}")
                if not await self.wait_for_position(conn, account_id, contract_id, 0):
                    return answer(False, error=f"closed the {current:g} it held but tradovate does not show it flat yet, nothing sent")
                notes.append(f"closed {current:g} first")
                current = 0
            elif not current and await self.open_brackets(conn, account_id, contract_id):
                # flat, but an old bracket still has orders working that could open a position
                await self.cancel_open_orders(conn, account_id, contract_id, [])

            body = self.bracket_order(signal, plan, contract, account_id, account_name, tick)
            # never resent, same as plain orders
            data = await conn.request("POST", "/orderStrategy/startOrderStrategy", body, retry=False)
            strategy = data.get("orderStrategy") if isinstance(data, dict) else None
            response = data if isinstance(data, dict) else None
            if not strategy:
                error = (response or {}).get("errorText") or failure_of(response) or "tradovate did not start the bracket"
                return answer(False, error=error, response=response)
            strategy_id = strategy["id"]
            await self.brackets.remember(conn.label, account_id, contract_id, strategy_id)

            entry_id = await self.entry_order(conn, strategy_id)
            if entry_id is None:
                return answer(False, error="tradovate started the bracket but shows no entry order, check this account",
                              response=response, strategy_id=strategy_id)
            status = await self.wait_for_status(conn, entry_id, signal.order_type == "market")
            fill_price = await self.get_fill_price(conn, account_id, entry_id)

            error = None
            if status in REJECTED:
                error = f"order was {status}"
            else:
                # the brackets show up right after the fill, or right away (waiting) for limit and stop entries
                orders = await self.bracket_orders(conn, strategy_id, plan)
                if orders is None:
                    error = "the take profits and stop did not show up on tradovate, check this account"
                elif plan.stop_price is not None and signal.order_type == "market" and fill_price is not None:
                    notes.append(await self.move_stops(conn, orders, plan, fill_price, side, tick, signal.is_automated))
                if error is None and status in ("Filled", "Completed"):
                    # the next signal reads the position, so wait until tradovate shows the fill
                    if not await self.wait_for_position(conn, account_id, contract_id, current + side * plan.total):
                        notes.append("position not confirmed yet")

            return answer(
                error is None,
                status=", ".join([status or "sent"] + notes),
                error=error,
                order_id=entry_id,
                fill_price=fill_price,
                response=response,
                strategy_id=strategy_id,
            )
        except ValueError as exc:
            # prices that do not work for this contract, nothing was sent
            return answer(False, error=str(exc))
        except Exception as exc:
            logger.error("[%s] bracket failed on account %s: %s", conn.label, account_id, exc)
            payload = exc.payload if isinstance(getattr(exc, "payload", None), dict) else None
            return answer(False, error=str(exc), response=payload)

    # the startOrderStrategy body. every distance is in points from the entry and has a sign:
    # a buy's target is above (+) and its stop below (-), a sell is the other way round
    def bracket_order(self, signal, plan, contract, account_id, account_name, tick):
        side = 1 if signal.action == "buy" else -1
        stop = self.stop_distance(signal, plan, tick)

        trigger = None
        if plan.breakeven_after:
            trigger = to_tick(plan.parts[plan.breakeven_after - 1].points, tick)
            offset = plan.breakeven_ticks * tick
            if offset >= trigger:
                raise ValueError(f"breakeven_ticks puts the moved stop at or past take profit {plan.breakeven_after}")
            if offset <= -stop:
                raise ValueError("breakeven_ticks puts the moved stop further away than the stop itself")

        brackets = []
        for part in plan.parts:
            bracket = {"qty": part.qty, "stopLoss": -side * stop, "trailingStop": False}
            target = None
            if part.points is not None:
                target = max(to_tick(part.points, tick), tick)
                bracket["profitTarget"] = side * target
            if trigger is not None and (target is None or target > trigger):
                # tradovate moves this stop once price is trigger points in profit. the trigger is
                # always positive, the offset goes the same way as the target
                bracket["breakeven"] = trigger
                bracket["breakevenPlus"] = side * plan.breakeven_ticks * tick
            brackets.append(bracket)

        entry = {"orderQty": plan.total, "orderType": signal.order_type.capitalize()}
        if signal.order_type == "limit":
            entry["price"] = signal.price
        if signal.order_type == "stop":
            entry["stopPrice"] = signal.price
        return {
            "accountId": account_id,
            "accountSpec": account_name,
            "symbol": contract,
            "action": signal.action.capitalize(),
            "orderStrategyTypeId": MULTIBRACKET,
            "params": json.dumps({"entryVersion": entry, "brackets": brackets}),
            "isAutomated": signal.is_automated,
        }

    # how far the stop goes from the entry, in points
    def stop_distance(self, signal, plan, tick):
        side = 1 if signal.action == "buy" else -1
        if plan.stop_loss is not None:
            distance = plan.stop_loss
        elif signal.price is not None:
            # stop_price measured from the limit or stop price, or from the alert's price on a market order
            distance = (signal.price - plan.stop_price) * side
            if distance <= 0:
                raise ValueError(f"stop_price {plan.stop_price:g} is on the wrong side of {signal.price:g}, nothing sent")
        else:
            # a market order without a price: start at the backup (or max) stop and move it to
            # stop_price once the fill is known
            distance = plan.backup_stop_loss or plan.max_stop_loss
            if distance is None:
                raise ValueError("a market order with stop_price also needs price (the alert's {{close}}) "
                                 "or a preset with a backup or max stop, nothing sent")
        if plan.max_stop_loss:
            distance = min(distance, plan.max_stop_loss)
        return max(to_tick(distance, tick), tick)

    async def entry_order(self, conn, strategy_id, tries=10, delay=0.2):
        for _ in range(tries):
            links = await conn.request("GET", f"/orderStrategyLink/deps?masterid={strategy_id}") or []
            for link in links:
                if link.get("label") == ENTRY_LABEL:
                    return link.get("orderId")
            await asyncio.sleep(delay)
        return None

    # bracket number -> its order ids, once every part has its stop (and its take profit if it
    # has one). None when they do not all show up
    async def bracket_orders(self, conn, strategy_id, plan, tries=20, delay=0.25):
        wanted = {str(i): 1 if part.points is None else 2 for i, part in enumerate(plan.parts)}
        for _ in range(tries):
            links = await conn.request("GET", f"/orderStrategyLink/deps?masterid={strategy_id}") or []
            orders = {}
            for link in links:
                if link.get("label") != ENTRY_LABEL:
                    orders.setdefault(link.get("label"), []).append(link.get("orderId"))
            if all(len(orders.get(label, [])) >= count for label, count in wanted.items()):
                return orders
            await asyncio.sleep(delay)
        return None

    # the alert gave an exact stop price but tradovate places stops at a distance from the fill,
    # so once the fill is known the stops are moved there. breakeven keeps working after this
    async def move_stops(self, conn, orders, plan, fill, side, tick, is_automated):
        target = plan.stop_price
        if plan.max_stop_loss and (fill - target) * side > plan.max_stop_loss:
            target = fill - side * plan.max_stop_loss
        target = to_tick(target, tick)
        if (fill - target) * side <= 0:
            return f"stop_price {plan.stop_price:g} was already past the fill, the stop stayed where it was"

        problems = []
        for order_ids in orders.values():
            for order_id in order_ids:
                version = await self.last_version(conn, order_id)
                if version.get("orderType") != "Stop" or version.get("stopPrice") == target:
                    continue
                body = {
                    "orderId": order_id,
                    "orderQty": version.get("orderQty"),
                    "orderType": "Stop",
                    "stopPrice": target,
                    "isAutomated": is_automated,
                }
                answer = await conn.request("POST", "/order/modifyorder", body)
                failure = (answer or {}).get("errorText") or failure_of(answer)
                if failure:
                    problems.append(f"order {order_id}: {failure}")
        if problems:
            return f"could not move every stop to {target:g} ({'; '.join(problems)})"
        return f"stop at {target:g}"

    # the current price and type of an order (the order itself does not have them)
    async def last_version(self, conn, order_id):
        versions = await conn.request("GET", f"/orderVersion/deps?masterid={order_id}") or []
        return versions[-1] if versions else {}

    # the order strategies localfill started on this account and contract that are still running.
    # only asks tradovate when there are some, so plain orders stay as fast as before
    async def open_brackets(self, conn, account_id, contract_id):
        ids = await self.brackets.tracked(conn.label, account_id, contract_id)
        if not ids:
            return []
        items = await conn.request("GET", f"/orderStrategy/items?ids={','.join(str(i) for i in ids)}") or []
        running = [item.get("id") for item in items if item.get("status") == "ActiveStrategy"]
        await self.brackets.forget([i for i in ids if i not in running])
        return running

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
