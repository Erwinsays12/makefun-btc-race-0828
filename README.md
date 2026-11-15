# Capitulation Sniper — cbBTC trading bot

Entry for `btc-race-24h-20261115-0828` (make.fun): a bot that trades Coinbase
Wrapped BTC (cbBTC) against USDC on 1-minute bars, starting with $1,000 of
paper money.

## Idea

A full-size round trip costs about 42 bps (4 bps fee + 2 bps slippage +
~15 bps of pool impact per side), while a typical 1-minute move is only
~12 bps of noise — so scalping is hopeless. The only trades worth taking
are the rare, violent capitulations: BTC falls hard for an hour, exhausts
itself, and snaps back several hundred bps. This bot waits for exactly that
shape and otherwise stays in cash.

## Strategy

One position at a time, long only, no leverage, fully deterministic (signals
come only from the bars the harness sent and the account it reports — no
clock, no network, no randomness).

- **Alpha leg (full size).** Buys when the 60-bar return is below −250 bps
  (capitulation), the 3-bar return has turned positive (the bounce has
  started), and the 15-bar return is above −100 bps (the fall is no longer
  accelerating). Exits on a 70 bps trailing stop below the peak since entry,
  or a 120 bps stop loss below the entry price. 5-bar cooldown after exits.
- **Compliance leg (10% size).** If no trade has happened by batch 1100, it
  takes one small trade on a modest dip (60-bar return below −60 bps with a
  positive 3-bar turn), so the entry always meets the guardrail of at least
  one trade. Trails at 40 bps, gives up after 90 bars.
- No new entries after batch 1400.

## Local result (dev window, kit's own grader)

- Net PnL: **+38.51 USDC** on the 24-hour dev window (buy-and-hold: −44.01)
- Trades: 2 (one full-size round trip), max drawdown 0.9%

## Run

The entrypoint is `main.py` (Python 3.13, standard library only). The
`vendor/` directory holds a byte-for-byte copy of the kit's
`sdk/makefun_bars.py` shim, which speaks the `agent-fun/bars-replay/v1`
protocol on stdin/stdout.

## License

MIT — see `LICENSE`.
