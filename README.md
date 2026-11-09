# Black Friday Mac deal watcher

Watches deal feeds for **Mac mini, MacBook Pro, Mac Studio** (US), pings Discord when a deal
clears your thresholds, and keeps a ranked leaderboard of every deal collected since you started.

Python 3.10+, standard library only. No installs.

## Setup
1. Discord: channel settings → Integrations → Webhooks → New Webhook → copy URL.
2. `export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'`
3. `python -m bfwatch test-discord`   (confirms the webhook works)
4. `python -m bfwatch run`            (polls every 15 min; daily top-10 digest)
   or cron: `*/15 * * * * cd /path/bf-watcher && DISCORD_WEBHOOK_URL=... python -m bfwatch once`

Other commands: `rank [-n 20] [--product "Mac mini"]`, `digest`, `parse "<deal title>"`, `--dry-run`.
Tests: `python -m unittest discover -s tests`.

## How it works
- `config.json` — sources (RSS/Atom), thresholds (`min_discount_pct` 10, `min_savings_usd` 100),
  poll/digest intervals, `include_refurbished` (default off: refurb "% off" isn't comparable).
- `baselines.json` — the **typical/launch price per config**; discount % is measured against it.
  Edit it as Apple's lineup changes. Titles with a chip/config not listed are still stored, but
  can only be judged by a stated "$X off".
- Rank = highest % off, then $ saved; deals with unknown baseline sort last. Stored in `deals.db`
  (SQLite), de-duplicated across sources by product+config+retailer+price.
- Discord alerts show price, typical price, savings, retailer and "Rank #N of M so far".
- Old items (> `max_age_hours`) and sub-threshold deals are recorded silently, so the first
  run doesn't spam you. At most 8 alerts/cycle; the rest go out next cycle.

## Caveats
- Sources are Slickdeals search RSS + r/AppleDeals RSS. I couldn't reach them from the build
  sandbox, so the feed URLs/format are untested live — run `python -m bfwatch once --dry-run`
  first. If a feed 403s (Reddit sometimes does), add other feeds to `sources`.
- Parsing is title-based and heuristic. Check `rank` output now and then and use
  `parse "title"` to debug misreads.
- Baselines are US prices from my knowledge and may be out of date.

## Vietnam: Tammi Watch Kid Plus / Max
`vn_watches` in `config.json` lists store pages per product. Each poll saves a price only when it
changes (a price history). Discord pings on a drop of 3% or more (`vn_min_drop_pct`), a new lowest
price seen, or back in stock. A daily "current prices + lowest seen" summary is also posted.
`python -m bfwatch vn` prints the current prices.

Tested from GitHub Actions: CellphoneS and Hoàng Hà Mobile work. Thế Giới Di Động and Điện Máy Xanh
time out from GitHub's US servers (disabled; enable them if you run this from a machine in Vietnam),
Shopee returns 403 to bots, and Tiki's API is intermittently 403 (disabled).
