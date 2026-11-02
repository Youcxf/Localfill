import asyncio
import itertools
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
        # seconds every placeorder takes, to test what happens when orders are slow
        self.order_delay = 0.0

        # what we received, so tests can look at it
        self.logins = []
        self.order_requests = []

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
        return web.json_response(ACCOUNTS)

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

    # ---------- orders ----------

    async def place_order(self, request):
        body = await request.json()
        self.order_requests.append(body)
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
        self.orders[order_id] = {"id": order_id, "accountId": body["accountId"], "ordStatus": status}
        if is_market and status == "Filled":
            self.add_fill(body["accountId"], contract, body["action"], body["orderQty"], order_id)
        return web.json_response({"orderId": order_id})

    async def order_item(self, request):
        order = self.orders.get(int(request.query["id"]))
        if order is None:
            return web.json_response({"errorText": "not found"}, status=404)
        return web.json_response(order)

    def add_fill(self, account_id, contract, action, qty, order_id):
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
        order_id = next(self.ids)
        action = "Sell" if position["netPos"] > 0 else "Buy"
        self.orders[order_id] = {"id": order_id, "accountId": body["accountId"], "ordStatus": "Filled"}
        self.add_fill(body["accountId"], contract, action, abs(position["netPos"]), order_id)
        return web.json_response({"orderId": order_id})

    # ---------- lists ----------

    # note: this one ignores the accountId in the url on purpose (so the tests can check
    # that we never touch another account's positions), the fill lists below honor it
    async def position_list(self, request):
        rows = []
        for (account_id, contract_id), position in self.positions.items():
            rows.append({
                "id": account_id * 100 + contract_id, "accountId": account_id, "contractId": contract_id,
                "netPos": position["netPos"], "netPrice": position["netPrice"],
            })
        return web.json_response(rows)

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
    app.router.add_post("/v1/order/placeorder", broker.place_order)
    app.router.add_get("/v1/order/item", broker.order_item)
    app.router.add_post("/v1/order/liquidateposition", broker.liquidate)
    app.router.add_get("/v1/position/list", broker.position_list)
    app.router.add_get("/v1/fill/list", broker.fill_list)
    app.router.add_get("/v1/fillPair/list", broker.pair_list)
    app.router.add_get("/v1/cashBalance/list", broker.cash_list)
    return app


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    web.run_app(make_app(), host="0.0.0.0", port=port)
