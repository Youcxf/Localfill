import asyncio
import itertools
import json
import math
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

from aiohttp import web

# a fake tradovate, so you can try localfill without a real account.
# the tests use it too.
#
#   python -m app.devtools.fake_tradovate
#
# then start localfill with LOCALFILL_TRADOVATE_DEMO_URL=http://127.0.0.1:9000/v1
# and add a connection with any username and password (the username "baduser" is rejected)

ACCOUNTS = [
    {"id": 1001, "name": "DEMO1001", "active": True},
    {"id": 1002, "name": "DEMO1002", "active": True},
]

CONTRACTS = [
    {"id": 1, "name": "MNQZ6", "contractMaturityId": 11},
    {"id": 2, "name": "MNQH7", "contractMaturityId": 12},
    {"id": 3, "name": "ESZ6", "contractMaturityId": 13},
    {"id": 4, "name": "ESH7", "contractMaturityId": 14},
]
FRONT_MATURITIES = [11, 13]

# dollars per point
POINT_VALUE = {"MNQ": 2.0, "ES": 50.0}
BASE_PRICE = {"MNQ": 20000.0, "ES": 6000.0}
TICK_SIZE = {"MNQ": 0.25, "ES": 0.25}

# in /orderStrategyLink the entry order has this label, the brackets have "0", "1"...
ENTRY_LABEL = "256"


def now_text():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FakeBroker:
    def __init__(self):
        self.ids = itertools.count(1)
        self.tokens = set()
        self.orders = {}
        self.fills = []
        self.pairs = []
        # (account id, contract id) -> {"netPos": ..., "netPrice": ...}
        self.positions = {}
        self.realized = {a["id"]: 0.0 for a in ACCOUNTS}

        # switches the tests use to make things go wrong
        self.fixed_price = None
        self.throttle_next_login = False
        self.refuse_orders = False
        self.reject_after_accept = False
        # answer placeorder with this http error, after the order was really placed
        self.order_error_status = None
        # answer the next placeorder with a time penalty instead of placing it
        self.penalize_next_order = False
        # after each fill, this many position reads still return the positions from before it
        # (tradovate's position list can lag behind fills)
        self.stale_reads_after_fill = 0
        self.stale_position_reads = 0
        self.position_snapshot = {}
        # account names that tradovate reports as closed, and accounts added later
        self.closed_accounts = set()
        self.extra_accounts = []
        self.cancelled_orders = []
        # seconds every placeorder takes, to test what happens when orders are slow
        self.order_delay = 0.0

        # order strategies (brackets): id -> strategy
        self.strategies = {}
        # answer startOrderStrategy with this errorText instead of starting it
        self.refuse_strategies = None
        # start strategies without placing their take profits and stop
        self.skip_brackets = False

        # what we received, so tests can look at it
        self.logins = []
        self.order_requests = []
        self.strategy_requests = []
        self.modify_requests = []

    def price_of(self, contract):
        if self.fixed_price is not None:
            return self.fixed_price
        root = contract["name"][:-2]
        # moves a little over time so pnl is not always zero
        return BASE_PRICE[root] + round(math.sin(time.time() / 20) * 40) / 4

    # ---------- auth ----------

    async def login(self, request):
        body = await request.json()
        self.logins.append(body)
        if self.throttle_next_login and "p-ticket" not in body:
            self.throttle_next_login = False
            return web.json_response({"p-ticket": "ticket-abc", "p-time": 0})
        if body.get("name") == "baduser":
            return web.json_response({"errorText": "Incorrect username or password"})
        return web.json_response(self.new_token())

    async def renew(self, request):
        return web.json_response(self.new_token())

    def new_token(self):
        token = secrets.token_hex(16)
        self.tokens.add(token)
        expires = datetime.now(timezone.utc) + timedelta(minutes=80)
        return {
            "accessToken": token,
            "expirationTime": expires.isoformat().replace("+00:00", "Z"),
            "userId": 42,
        }

    # ---------- accounts and contracts ----------

    async def account_list(self, request):
        rows = [dict(a, closed=a["name"] in self.closed_accounts) for a in ACCOUNTS + self.extra_accounts]
        return web.json_response(rows)

    async def contract_suggest(self, request):
        text = request.query.get("t", "").upper()
        # sorted by name, so the front month is not always first (like the real one)
        matches = sorted((c for c in CONTRACTS if c["name"].startswith(text)), key=lambda c: c["name"])
        return web.json_response(matches)

    async def maturity_item(self, request):
        maturity_id = int(request.query["id"])
        return web.json_response({"id": maturity_id, "isFront": maturity_id in FRONT_MATURITIES})

    async def contract_item(self, request):
        contract_id = int(request.query["id"])
        found = [c for c in CONTRACTS if c["id"] == contract_id]
        return web.json_response(found[0] if found else {})

    async def product_find(self, request):
        name = request.query.get("name", "")
        if name not in TICK_SIZE:
            return web.json_response({"errorText": "not found"}, status=404)
        return web.json_response({"name": name, "tickSize": TICK_SIZE[name], "valuePerPoint": POINT_VALUE[name]})

    # ---------- orders ----------

    async def place_order(self, request):
        body = await request.json()
        self.order_requests.append(body)
        if self.penalize_next_order and "p-ticket" not in body:
            self.penalize_next_order = False
            return web.json_response({"p-ticket": "penalty-ticket", "p-time": 0})
        await asyncio.sleep(self.order_delay)
        if self.refuse_orders:
            return web.json_response({"failureReason": "TradingLocked", "failureText": "trading is locked"})
        found = [c for c in CONTRACTS if c["name"] == body["symbol"]]
        if not found:
            return web.json_response({"failureReason": "InvalidContract", "failureText": "unknown symbol"})

        contract = found[0]
        order_id = next(self.ids)
        is_market = body["orderType"] == "Market"
        if self.reject_after_accept:
            status = "Rejected"
        else:
            status = "Filled" if is_market else "Working"
        self.orders[order_id] = {
            "id": order_id, "accountId": body["accountId"], "contractId": contract["id"],
            "action": body["action"], "ordStatus": status,
        }
        if is_market and status == "Filled":
            self.add_fill(body["accountId"], contract, body["action"], body["orderQty"], order_id)
        if self.order_error_status:
            return web.json_response({"errorText": "gateway timeout"}, status=self.order_error_status)
        return web.json_response({"orderId": order_id})

    async def order_item(self, request):
        order = self.orders.get(int(request.query["id"]))
        if order is None:
            return web.json_response({"errorText": "not found"}, status=404)
        return web.json_response(order)

    def add_fill(self, account_id, contract, action, qty, order_id):
        self.position_snapshot = {key: dict(value) for key, value in self.positions.items()}
        self.stale_position_reads = self.stale_reads_after_fill
        price = self.price_of(contract)
        fill_id = next(self.ids)
        self.fills.append({
            "id": fill_id, "orderId": order_id, "accountId": account_id, "contractId": contract["id"],
            "action": action, "qty": qty, "price": price, "timestamp": now_text(),
        })

        position = self.positions.setdefault((account_id, contract["id"]), {"netPos": 0, "netPrice": 0.0})
        sign = 1 if action == "Buy" else -1
        old = position["netPos"]
        new = old + sign * qty

        # the part of this fill that closes an existing position makes a pair
        if old * sign < 0:
            closed = min(qty, abs(old))
            entry = position["netPrice"]
            buy_price, sell_price = (entry, price) if old > 0 else (price, entry)
            self.pairs.append({
                "buyPrice": buy_price, "sellPrice": sell_price, "qty": closed,
                "buyFillId": fill_id, "sellFillId": fill_id, "archived": False, "accountId": account_id,
            })
            self.realized[account_id] += (sell_price - buy_price) * closed * POINT_VALUE[contract["name"][:-2]]

        if old == 0 or old * sign > 0:
            total = abs(old) + qty
            position["netPrice"] = (position["netPrice"] * abs(old) + price * qty) / total
        elif new == 0:
            position["netPrice"] = 0.0
        elif old * new < 0:
            position["netPrice"] = price
        position["netPos"] = new

    async def liquidate(self, request):
        body = await request.json()
        key = (body["accountId"], body["contractId"])
        position = self.positions.get(key)
        if not position or position["netPos"] == 0:
            return web.json_response({"failureReason": "UnknownReason", "failureText": "no position"})
        contract = [c for c in CONTRACTS if c["id"] == body["contractId"]][0]
        for strategy in self.strategies.values():
            if strategy["accountId"] == body["accountId"] and strategy["contractId"] == body["contractId"]:
                self.end_strategy(strategy, "ExecutionInterrupted")
        order_id = next(self.ids)
        action = "Sell" if position["netPos"] > 0 else "Buy"
        self.orders[order_id] = {"id": order_id, "accountId": body["accountId"], "ordStatus": "Filled"}
        self.add_fill(body["accountId"], contract, action, abs(position["netPos"]), order_id)
        return web.json_response({"orderId": order_id})

    # ---------- order strategies (brackets) ----------

    # like the real one: the entry, then per bracket a take profit (limit) and a stop, each pair
    # cancels its other side when one fills. a plain order that closes the position leaves them
    # working, liquidate cancels them
    async def start_strategy(self, request):
        body = await request.json()
        self.strategy_requests.append(body)
        if self.refuse_strategies:
            return web.json_response({"errorText": self.refuse_strategies})
        found = [c for c in CONTRACTS if c["name"] == body["symbol"]]
        if not found:
            return web.json_response({"errorText": "unknown symbol"})
        contract = found[0]
        params = json.loads(body["params"])
        entry = params["entryVersion"]

        strategy_id = next(self.ids)
        strategy = {
            "id": strategy_id, "accountId": body["accountId"], "contractId": contract["id"],
            "action": body["action"], "status": "ActiveStrategy", "params": body["params"],
            "brackets": params["brackets"],
        }
        self.strategies[strategy_id] = strategy

        entry_id = next(self.ids)
        is_market = entry["orderType"] == "Market"
        self.orders[entry_id] = {
            "id": entry_id, "accountId": body["accountId"], "contractId": contract["id"], "action": body["action"],
            "ordStatus": "Filled" if is_market else "Working", "orderType": entry["orderType"],
            "orderQty": entry["orderQty"], "price": entry.get("price"), "stopPrice": entry.get("stopPrice"),
            "strategyId": strategy_id, "label": ENTRY_LABEL,
        }
        if is_market:
            self.add_fill(body["accountId"], contract, body["action"], entry["orderQty"], entry_id)
            self.place_brackets(strategy, self.fills[-1]["price"], "Working")
        else:
            # waiting for the entry, priced from the entry price
            self.place_brackets(strategy, entry.get("price") or entry.get("stopPrice"), "Suspended")
        answer = {key: strategy[key] for key in ("id", "accountId", "contractId", "action", "status", "params")}
        return web.json_response({"orderStrategy": answer})

    def place_brackets(self, strategy, entry_price, status):
        if self.skip_brackets:
            return
        side = 1 if strategy["action"] == "Buy" else -1
        exit_action = "Sell" if side == 1 else "Buy"
        for number, bracket in enumerate(strategy["brackets"]):
            common = {
                "accountId": strategy["accountId"], "contractId": strategy["contractId"], "action": exit_action,
                "ordStatus": status, "orderQty": bracket["qty"], "strategyId": strategy["id"], "label": str(number),
            }
            if "profitTarget" in bracket:
                order_id = next(self.ids)
                target = entry_price + bracket["profitTarget"]
                self.orders[order_id] = dict(common, id=order_id, orderType="Limit", price=target, stopPrice=None)
            order_id = next(self.ids)
            stop = dict(common, id=order_id, orderType="Stop", price=None, stopPrice=entry_price + bracket["stopLoss"])
            if "breakeven" in bracket:
                # once price is this far in profit the stop goes to entry + breakevenPlus
                stop["breakeven"] = (entry_price + side * bracket["breakeven"], entry_price + bracket.get("breakevenPlus", 0))
            self.orders[order_id] = stop

    def strategy_orders(self, strategy):
        return [o for o in self.orders.values() if o.get("strategyId") == strategy["id"]]

    def end_strategy(self, strategy, status):
        if strategy["status"] != "ActiveStrategy":
            return
        for order in self.strategy_orders(strategy):
            if order["ordStatus"] in ("Working", "Suspended"):
                order["ordStatus"] = "Canceled"
                self.cancelled_orders.append(order["id"])
        strategy["status"] = status

    # tests call this to move the market: stops move to breakeven, brackets fill and cancel their other side
    def move_to(self, price):
        self.fixed_price = price
        for strategy in self.strategies.values():
            if strategy["status"] != "ActiveStrategy":
                continue
            contract = [c for c in CONTRACTS if c["id"] == strategy["contractId"]][0]
            orders = self.strategy_orders(strategy)
            entry = [o for o in orders if o["label"] == ENTRY_LABEL][0]
            if entry["ordStatus"] == "Working" and self.touches(entry, price):
                entry["ordStatus"] = "Filled"
                self.add_fill(strategy["accountId"], contract, entry["action"], entry["orderQty"], entry["id"])
                for order in orders:
                    if order["ordStatus"] == "Suspended":
                        order["ordStatus"] = "Working"
            for order in orders:
                if order["ordStatus"] != "Working" or order["label"] == ENTRY_LABEL:
                    continue
                if order.get("breakeven"):
                    trigger, new_stop = order["breakeven"]
                    if (order["action"] == "Sell" and price >= trigger) or (order["action"] == "Buy" and price <= trigger):
                        order["stopPrice"] = new_stop
                        order["breakeven"] = None
                if self.touches(order, price):
                    order["ordStatus"] = "Filled"
                    self.add_fill(strategy["accountId"], contract, order["action"], order["orderQty"], order["id"])
                    # the other side of the same bracket
                    for other in orders:
                        if other["label"] == order["label"] and other["ordStatus"] == "Working":
                            other["ordStatus"] = "Canceled"
            if not any(o["ordStatus"] in ("Working", "Suspended") for o in self.strategy_orders(strategy)):
                strategy["status"] = "Completed"

    # would this limit or stop order fill at price
    def touches(self, order, price):
        buy = order["action"] == "Buy"
        if order["orderType"] == "Limit":
            return price <= order["price"] if buy else price >= order["price"]
        if order["orderType"] == "Stop":
            return price >= order["stopPrice"] if buy else price <= order["stopPrice"]
        return False

    async def strategy_links(self, request):
        strategy = self.strategies.get(int(request.query["masterid"]))
        if strategy is None:
            return web.json_response([])
        rows = [{"id": o["id"], "orderId": o["id"], "label": o["label"]} for o in self.strategy_orders(strategy)]
        return web.json_response(rows)

    async def strategy_items(self, request):
        ids = [int(i) for i in request.query.get("ids", "").split(",") if i]
        keys = ("id", "accountId", "contractId", "status")
        return web.json_response([{k: s[k] for k in keys} for i, s in self.strategies.items() if i in ids])

    async def order_versions(self, request):
        order = self.orders.get(int(request.query["masterid"]))
        if order is None:
            return web.json_response([])
        keys = ("orderType", "orderQty", "price", "stopPrice")
        return web.json_response([dict({k: order.get(k) for k in keys}, orderId=order["id"])])

    async def modify_order(self, request):
        body = await request.json()
        self.modify_requests.append(body)
        order = self.orders.get(body["orderId"])
        if order is None or order["ordStatus"] not in ("Working", "Suspended"):
            return web.json_response({"failureReason": "TooLate"})
        for key in ("orderQty", "price", "stopPrice"):
            if key in body:
                order[key] = body[key]
        return web.json_response({"commandId": next(self.ids)})

    # ---------- lists ----------

    # note: this one ignores the accountId in the url on purpose (so the tests can check
    # that we never touch another account's positions), the fill lists below honor it
    async def position_list(self, request):
        positions = self.positions
        if self.stale_position_reads > 0:
            self.stale_position_reads -= 1
            positions = self.position_snapshot
        rows = []
        for (account_id, contract_id), position in positions.items():
            rows.append({
                "id": account_id * 100 + contract_id, "accountId": account_id, "contractId": contract_id,
                "netPos": position["netPos"], "netPrice": position["netPrice"],
            })
        return web.json_response(rows)

    # orders of one account (masterid = account id)
    async def order_deps(self, request):
        account_id = int(request.query["masterid"])
        return web.json_response([o for o in self.orders.values() if o["accountId"] == account_id])

    async def cancel_order(self, request):
        body = await request.json()
        order = self.orders.get(body["orderId"])
        if order is None or order["ordStatus"] not in ("Working", "Suspended"):
            return web.json_response({"failureReason": "UnknownReason", "failureText": "not working"})
        order["ordStatus"] = "Canceled"
        self.cancelled_orders.append(order["id"])
        strategy = self.strategies.get(order.get("strategyId"))
        if strategy and order.get("label") == ENTRY_LABEL:
            self.end_strategy(strategy, "ExecutionInterrupted")
        return web.json_response({"orderId": order["id"]})

    # fills of one order (masterid = order id)
    async def fill_deps(self, request):
        order_id = int(request.query["masterid"])
        return web.json_response([f for f in self.fills if f["orderId"] == order_id])

    async def fill_list(self, request):
        return web.json_response(self.for_account(self.fills, request))

    async def pair_list(self, request):
        return web.json_response(self.for_account(self.pairs, request))

    def for_account(self, rows, request):
        account_id = request.query.get("accountId")
        if account_id is None:
            return rows
        return [row for row in rows if row["accountId"] == int(account_id)]

    async def cash_list(self, request):
        today = datetime.now(timezone.utc)
        rows = []
        for account in ACCOUNTS:
            rows.append({
                "accountId": account["id"], "timestamp": now_text(),
                "tradeDate": {"year": today.year, "month": today.month, "day": today.day},
                "realizedPnL": self.realized[account["id"]],
            })
        return web.json_response(rows)


def make_app(broker=None):
    broker = broker or FakeBroker()

    # every call except the login needs a token we handed out
    @web.middleware
    async def check_token(request, handler):
        if not request.path.endswith("accesstokenrequest"):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token not in broker.tokens:
                return web.json_response({"errorText": "unauthorized"}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[check_token])
    app.router.add_post("/v1/auth/accesstokenrequest", broker.login)
    app.router.add_post("/v1/auth/renewaccesstoken", broker.renew)
    app.router.add_get("/v1/account/list", broker.account_list)
    app.router.add_get("/v1/contract/suggest", broker.contract_suggest)
    app.router.add_get("/v1/contractMaturity/item", broker.maturity_item)
    app.router.add_get("/v1/contract/item", broker.contract_item)
    app.router.add_get("/v1/product/find", broker.product_find)
    app.router.add_post("/v1/order/placeorder", broker.place_order)
    app.router.add_get("/v1/order/item", broker.order_item)
    app.router.add_post("/v1/order/liquidateposition", broker.liquidate)
    app.router.add_get("/v1/position/list", broker.position_list)
    app.router.add_get("/v1/fill/list", broker.fill_list)
    app.router.add_get("/v1/fill/deps", broker.fill_deps)
    app.router.add_get("/v1/order/deps", broker.order_deps)
    app.router.add_post("/v1/order/cancelorder", broker.cancel_order)
    app.router.add_get("/v1/fillPair/list", broker.pair_list)
    app.router.add_get("/v1/cashBalance/list", broker.cash_list)
    app.router.add_post("/v1/orderStrategy/startOrderStrategy", broker.start_strategy)
    app.router.add_get("/v1/orderStrategy/items", broker.strategy_items)
    app.router.add_get("/v1/orderStrategyLink/deps", broker.strategy_links)
    app.router.add_get("/v1/orderVersion/deps", broker.order_versions)
    app.router.add_post("/v1/order/modifyorder", broker.modify_order)
    return app


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    web.run_app(make_app(), host="0.0.0.0", port=port)
