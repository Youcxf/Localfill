import json
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models import Preset, TakeProfit, check_breakeven


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# the take profits and stop one signal uses, once the alert is mixed with its preset
@dataclass
class BracketPlan:
    parts: list
    # the stop, as points from the fill or as an exact price (only one is set)
    stop_loss: float | None
    stop_price: float | None
    # from the preset: the backup stop and the farthest stop allowed
    backup_stop_loss: float | None
    max_stop_loss: float | None
    breakeven_after: int | None
    breakeven_ticks: int
    if_open: str

    # contracts per account
    @property
    def total(self):
        return sum(part.qty for part in self.parts)

    # "5 at +8, 5 at +20, stop 12, breakeven after tp 1 +2 ticks"
    def describe(self):
        words = [f"{p.qty} at +{p.points:g}" if p.points else f"{p.qty} with only the stop" for p in self.parts]
        if self.stop_loss is not None:
            stop = f"stop {self.stop_loss:g}"
        else:
            stop = f"stop at {self.stop_price:g}"
        if self.max_stop_loss:
            stop += f" (max {self.max_stop_loss:g})"
        words.append(stop)
        if self.breakeven_after:
            words.append(f"breakeven after tp {self.breakeven_after} {self.breakeven_ticks:+d} ticks")
        if self.if_open != "reverse":
            words.append(f"if open: {self.if_open}")
        return ", ".join(words)


# bracket presets from the dashboard, and the order strategies localfill started on tradovate
class Brackets:
    def __init__(self, db):
        self.db = db

    # ---------- presets ----------

    async def presets(self):
        rows = await self.db.fetch_all("select * from presets order by name")
        return [self.to_preset(row) for row in rows]

    async def preset(self, name):
        row = await self.db.fetch_one("select * from presets where name = ?", (name,))
        return self.to_preset(row) if row else None

    # creates the preset or replaces the one with that name
    async def save_preset(self, name, config):
        await self.db.execute(
            "insert into presets (name, config, updated_at) values (?, ?, ?) "
            "on conflict(name) do update set config = excluded.config, updated_at = excluded.updated_at",
            (name, config.model_dump_json(), now_text()),
        )
        return await self.preset(name)

    # true if there was one to delete
    async def delete_preset(self, name):
        if await self.preset(name) is None:
            return False
        await self.db.execute("delete from presets where name = ?", (name,))
        return True

    def to_preset(self, row):
        return Preset(name=row["name"], **json.loads(row["config"]))

    # ---------- what one signal uses ----------

    # mixes the alert with its preset: a field in the alert wins over the same field in the preset.
    # raises ValueError when something needed is missing
    async def plan_for(self, signal):
        preset = None
        if signal.preset:
            preset = await self.preset(signal.preset)
            if preset is None:
                raise ValueError(f"there is no preset called '{signal.preset}'")

        parts = signal.take_profits
        if parts is None:
            parts = preset.take_profits if preset else []
        # no take profits at all: the whole qty with only a stop
        if not parts:
            parts = [TakeProfit(qty=signal.qty)]

        breakeven_after = signal.breakeven_after
        if breakeven_after is None and preset:
            breakeven_after = preset.breakeven_after
        check_breakeven(parts, breakeven_after)
        breakeven_ticks = signal.breakeven_ticks
        if breakeven_ticks is None:
            breakeven_ticks = preset.breakeven_ticks if preset else 0

        backup = preset.stop_loss if preset else None
        stop_loss = signal.stop_loss
        if stop_loss is None and signal.stop_price is None:
            stop_loss = backup
        if stop_loss is None and signal.stop_price is None:
            raise ValueError("a bracket needs a stop: send stop_loss or stop_price, or give the preset a backup stop")

        return BracketPlan(
            parts=list(parts),
            stop_loss=stop_loss,
            stop_price=signal.stop_price,
            backup_stop_loss=backup,
            # a safety limit, so only the preset sets it and an alert can not lift it
            max_stop_loss=preset.max_stop_loss if preset else None,
            breakeven_after=breakeven_after,
            breakeven_ticks=breakeven_ticks,
            if_open=signal.if_open or (preset.if_open if preset else "reverse"),
        )

    # ---------- strategies localfill started ----------

    async def remember(self, connection, account_id, contract_id, strategy_id):
        await self.db.execute(
            "insert or ignore into brackets (strategy_id, connection, account_id, contract_id, created_at) values (?, ?, ?, ?, ?)",
            (strategy_id, connection, account_id, contract_id, now_text()),
        )

    async def tracked(self, connection, account_id, contract_id):
        rows = await self.db.fetch_all(
            "select strategy_id from brackets where connection = ? and account_id = ? and contract_id = ?",
            (connection, account_id, contract_id),
        )
        return [row["strategy_id"] for row in rows]

    # every strategy localfill started on one account, newest first
    async def on_account(self, connection, account_id):
        rows = await self.db.fetch_all(
            "select strategy_id from brackets where connection = ? and account_id = ? order by created_at desc",
            (connection, account_id),
        )
        return [row["strategy_id"] for row in rows]

    # strategies tradovate is done with
    async def forget(self, strategy_ids):
        for strategy_id in strategy_ids:
            await self.db.execute("delete from brackets where strategy_id = ?", (strategy_id,))
