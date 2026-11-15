"""Capitulation Sniper: a cbBTC trading bot for agent-fun/bars-replay/v1.

Idea, from the dev window's own shape: on 1-minute bars a full-size round
trip costs about 42 bps (4 bps fee + 2 bps slippage + ~15 bps of pool impact
on each side), while a 1-minute move is only ~12 bps of noise. So scalping
is hopeless and the only trades worth taking are the rare, violent
capitulations: BTC falls hard for an hour, exhausts itself, and snaps back
several hundred bps. This bot waits for exactly that shape and otherwise
stays in cash.

Two legs, one position at a time, long only, no leverage:

  * Alpha leg (full size). Fires when the 60-bar return is below -250 bps
    (a capitulation), the 3-bar return has turned positive (the bounce has
    started) and the 15-bar return is above -100 bps (the fall is no longer
    accelerating). Exits on a 70 bps trailing stop below the peak since
    entry, or a 120 bps stop loss below the entry price. A 5-bar cooldown
    after every exit keeps it from re-entering the same falling knife.

  * Compliance leg (10% size). If no trade has happened by batch 1100 and
    the market shows even a modest dip (60-bar return below -60 bps with a
    positive 3-bar turn), it takes one small trade so the entry always meets
    the guardrail of at least one trade, at a cost of a few cents in the
    worst case. It trails at 40 bps and gives up after 90 bars.

No entries after batch 1400, so nothing is left to the end-of-window
flatten except a position the trailing stop is already guarding.

Everything is deterministic: signals come only from the bars the harness
sent and the account it reports. No clock, no network, no randomness, no
environment input. The same bars always produce the same orders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Alpha leg.
DROP_BPS = 250            # 60-bar fall that counts as a capitulation
CONFIRM_BPS = 10          # 3-bar return that says the bounce has started
FALL_FLOOR_BPS = -100     # 15-bar return must be above this (not accelerating down)
TRAIL_BPS = 70            # trailing stop below the peak since entry
STOP_BPS = 120            # stop loss below the entry price
COOLDOWN_BARS = 5         # bars to wait after an exit before re-entering
LAST_ENTRY_BAR = 1400     # no new entries from this batch on

# Compliance leg (small, late, only when nothing has traded yet).
COMP_AFTER_BAR = 1100     # earliest batch the compliance leg may fire
COMP_DROP_BPS = 60        # modest dip that is enough for a small trade
COMP_CONFIRM_BPS = 5
COMP_SIZE_BPS = 1000      # 10% of cash
COMP_TRAIL_BPS = 40
COMP_MAX_HOLD_BARS = 90

BUY_SLIPPAGE_BPS = 300    # far above the largest 1-minute move seen (~80 bps)
SELL_SLIPPAGE_BPS = 500


def _ret_bps(closes, now, back):
    """Return in bps of closes[now] vs closes[now - back]."""
    return (closes[now] - closes[now - back]) * 10000.0 / closes[now - back]


class Sniper:
    """Keeps the bar history and the open-trade state; decides one reply."""

    def __init__(self):
        self.closes = []
        self.peak = None       # highest close since entry
        self.entry = None      # close at entry
        self.held = 0          # bars held in the current position
        self.cool = 0          # cooldown bars remaining
        self.trades = 0        # buys taken so far (alpha + compliance)
        self.comp = False      # the open position is the compliance leg
        self.trail_bps = TRAIL_BPS

    def on_batch(self, state, batch):
        # Fold this batch's bar into history (a gap minute carries no bar).
        for bar in batch.get("bars", []):
            if bar.get("symbol") == SYMBOL:
                self.closes.append(int(bar["c"]))
        if self.cool > 0:
            self.cool -= 1

        # Never order on the final batch: its orders are rejected (after_end)
        # and the harness flattens any open position at the last close.
        if state.seq >= 1439:
            return []

        holding = state.positions.get(SYMBOL)
        if holding is None or holding.qty <= 0:
            return self._maybe_enter(state)
        return self._maybe_exit(state, holding)

    def _maybe_enter(self, state):
        if self.cool > 0 or state.seq >= LAST_ENTRY_BAR:
            return []
        if state.warming_up or len(self.closes) < 64:
            return []
        n = len(self.closes) - 1
        r60 = _ret_bps(self.closes, n, 60)
        r15 = _ret_bps(self.closes, n, 15)
        r3 = _ret_bps(self.closes, n, 3)

        # Alpha leg: capitulation + bounce starting + fall not accelerating.
        if r60 < -DROP_BPS and r3 > CONFIRM_BPS and r15 > FALL_FLOOR_BPS:
            self._open(n, TRAIL_BPS, comp=False)
            return [{
                "side": "buy",
                "symbol": SYMBOL,
                "notional_quote_units": state.cash,
                "max_slippage_bps": BUY_SLIPPAGE_BPS,
            }]
        # Compliance leg: one small trade, late, only if nothing traded yet.
        if (self.trades == 0 and not self.comp and state.seq >= COMP_AFTER_BAR
                and r60 < -COMP_DROP_BPS and r3 > COMP_CONFIRM_BPS):
            self._open(n, COMP_TRAIL_BPS, comp=True)
            amount = state.cash * COMP_SIZE_BPS // 10000
            return [{
                "side": "buy",
                "symbol": SYMBOL,
                "notional_quote_units": amount,
                "max_slippage_bps": BUY_SLIPPAGE_BPS,
            }]
        return []

    def _open(self, n, trail_bps, comp):
        self.peak = self.closes[n]
        self.entry = self.closes[n]
        self.held = 0
        self.trades += 1
        self.comp = comp
        self.trail_bps = trail_bps

    def _maybe_exit(self, state, holding):
        self.held += 1
        close = self.closes[-1]
        if close > self.peak:
            self.peak = close
        sell = False
        if close < self.peak * (1 - self.trail_bps / 10000.0):
            sell = True
        if close < self.entry * (1 - STOP_BPS / 10000.0):
            sell = True
        if self.comp and self.held >= COMP_MAX_HOLD_BARS:
            sell = True
        if not sell:
            return []
        self.cool = COOLDOWN_BARS
        self.comp = False
        return [{
            "side": "sell",
            "symbol": SYMBOL,
            "qty": holding.qty,
            "max_slippage_bps": SELL_SLIPPAGE_BPS,
        }]


def on_batch(state, batch):
    return _BOT.on_batch(state, batch)


_BOT = Sniper()

makefun_bars.run(on_batch)
