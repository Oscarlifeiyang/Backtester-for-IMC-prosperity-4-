# R3 Backtest Dashboard Guide

This README explains how to use `r3_dashboard.html`, what each chart means, and how to turn the dashboard into trading decisions.

Dashboard file:

```text
r3_dashboard.html
```

Typical local path:

```text
C:/Users/oscar/Documents/CMU/IMC/imc4_five_guys_bt-main (1)/imc4_five_guys_bt-main/r3_dashboard.html
```

Open it directly in a browser. It is a standalone Plotly HTML file, so teammates do not need to run a server.

## How To Regenerate

From the project root:

```powershell
python -m prosperity4bt trader_r3.py 3 --data r3 --dashboard r3_dashboard.html --merge-pnl --no-progress --metrics
```

What this does:

- runs `trader_r3.py` on Round 3 days `0`, `1`, and `2`
- merges the days into one continuous timeline
- prints terminal metrics
- writes the interactive dashboard to `r3_dashboard.html`

## Dashboard Structure

The dashboard has three main tabs:

```text
1. Overview
2. Product Drilldown
3. Options IV
```

Use them in this order:

```text
Overview -> find important products/risk
Product Drilldown -> inspect one product deeply
Options IV -> analyze VEV option relative value
```

## Top Metric Cards

These cards appear at the top of the dashboard.

### Total PnL

Final cumulative profit across all products and selected days.

### Max Drawdown

Worst drop from a previous PnL peak.

Example:

```text
PnL reaches 80,000
PnL later falls to 55,000
Drawdown = -25,000
```

This measures risk. A strategy can have high final PnL but still be dangerous if drawdown is large.

### Sharpe

Average PnL increment divided by volatility of PnL increments.

This is not annualized. Treat it as a smoothness metric, not a professional finance Sharpe.

### Win Rate

Fraction of fills that were better than the contemporaneous mid price.

For buys:

```text
win if fill_price < mid
```

For sells:

```text
win if fill_price > mid
```

Important: a low win rate does not automatically mean the strategy is bad. If the strategy market-takes, it often crosses spread and gets negative mid edge by construction.

### Avg Mid Edge

Average execution quality versus mid price.

For buys:

```text
mid_edge = mid - fill_price
```

For sells:

```text
mid_edge = fill_price - mid
```

Interpretation:

```text
positive = filled better than mid
negative = paid spread or got adverse execution
```

### Trades

Total number of our executed trades.

### Book Fills

Fills matched immediately against visible order book liquidity in the local backtester.

### Tape Fills

Fills matched against market trades in the local backtester.

In the current R3 runs, this is usually `0`, meaning most fills are immediate book fills.

## Overview Tab

The Overview tab answers:

```text
Where did PnL come from?
How risky was the strategy?
Which products deserve investigation?
Which executions were worst?
```

### Total PnL Chart

Shows cumulative PnL over the merged timeline.

Use it to see whether PnL is steady or comes from a few lucky periods.

### Drawdown Chart

Shows how far PnL is below its previous peak.

Use it to find painful periods and recovery time.

### Product PnL Contribution

Horizontal bar chart of final PnL by product.

Use it to answer:

```text
Which products are actually making money?
Which products are hurting us?
```

### Average Mid Edge by Product

Horizontal bar chart of average fill edge versus mid.

Use it to check execution quality by product.

Note: negative edge is normal for market-taking, but extremely negative edge means the strategy may be overpaying, using stale fair values, or trading during bad liquidity.

### Product Scorecard

Table with one row per product.

Columns:

```text
Product       product symbol
Final PnL     final product-level profit
Trades        number of fills
Avg Mid Edge  average execution edge versus mid
Book          book fills
Tape          tape/passive fills
```

Use this table to decide which product to inspect in Product Drilldown.

### Worst Executions vs Mid

Table of the worst fills by mid edge.

Columns:

```text
Timestamp  when the fill happened
Product    product traded
Side       BUY or SELL
Price      fill price
Mid        mid price at that timestamp
Qty        fill quantity
Mid Edge   fill quality versus mid
Fill       book/tape fill type
```

Use this to find bad execution patterns.

## Product Drilldown Tab

This tab is for inspecting one product at a time.

Use the product dropdown at the top. Each chart rescales to the selected product, so low-priced options are not crushed by high-priced products.

### Mid Price and Trades

Shows:

```text
blue line       mid price
green markers   our buys
red markers     our sells
```

Use it to check:

```text
Did we buy before price rose?
Did we sell before price fell?
Are trades clustered in bad regions?
```

### PnL

Product-level cumulative PnL.

Use it to check if a product earns steadily or has one large jump/loss.

### Position

Inventory over time.

Dashed orange lines show the product position limits.

Use it to check:

```text
Are we stuck at the limit?
Are we long-only or short-only?
Are losses caused by holding too much inventory?
```

### Spread 150-Tick Rolling Mean

Smoothed bid-ask spread.

Raw spread is noisy, so this chart uses a rolling mean.

Use it to identify liquidity regimes:

```text
higher spread = expensive to trade aggressively
lower spread  = cheaper liquidity
```

### L1 Imbalance 150-Tick Rolling Mean

Smoothed top-of-book imbalance.

Formula:

```text
imbalance = (best_bid_volume - best_ask_volume) / (best_bid_volume + best_ask_volume)
```

Interpretation:

```text
positive = more bid size than ask size
negative = more ask size than bid size
near 0   = balanced top of book
```

### L1 Imbalance Z-Score - 50 Tick

Short-term imbalance surprise.

Use it for entry timing and fast pressure changes.

### L1 Imbalance Z-Score - 150 Tick

Medium-term imbalance surprise.

This is the default horizon for trading signal interpretation.

### L1 Imbalance Z-Score - 500 Tick

Slow regime pressure.

Use it to see whether imbalance is persistently unusual.

Z-score formula:

```text
z = (current_imbalance - rolling_mean) / rolling_std
```

Interpretation:

```text
z > +2  unusual bid-side pressure
z < -2  unusual ask-side pressure
near 0  normal
```

### Mid Edge vs Fill Price

Shows every fill's execution edge versus mid.

Green points:

```text
filled better than mid
```

Red points:

```text
filled worse than mid
```

Use it to diagnose whether poor PnL came from bad execution or bad strategy direction.

## Options IV Tab

This tab is specific to Round 3 VEV vouchers.

R3 products:

```text
VELVETFRUIT_EXTRACT = underlying
VEV_4000            = call option, strike 4000
VEV_4500            = call option, strike 4500
...
VEV_6500            = call option, strike 6500
```

The dashboard solves implied volatility from each option's market mid price.

### Downsampled Raw IV Surface

Axes:

```text
x-axis = timestamp
y-axis = strike
color  = implied volatility
```

Use it to see whether the overall option market is becoming more or less expensive.

White cells mean IV could not be solved reliably. Common reasons:

```text
missing/illiquid option book
option mid below intrinsic value
deep ITM/OTM instability
bad or incomplete market data
```

### Relative IV: Rich/Cheap Strikes

This is usually more useful than raw IV.

For each timestamp:

```text
relative_iv = strike_iv - median_iv_across_strikes
```

Interpretation:

```text
positive = strike is rich/expensive vs same-time option curve
negative = strike is cheap vs same-time option curve
near 0   = normal
```

This helps remove broad volatility regime and focus on cross-strike mispricing.

### Action View: Which Strike Is Rich/Cheap?

This summarizes the relative IV heatmap.

Top panel:

```text
blue dots = richest strike at that timestamp
red dots  = cheapest strike at that timestamp
```

Bottom panel:

```text
blue line = how rich the richest strike is
red line  = how cheap the cheapest strike is
```

Use it to answer:

```text
Which strike is consistently expensive?
Which strike is consistently cheap?
Is the rich/cheap signal large enough to trade?
```

### IV Smile Snapshots

Shows the IV curve at a few selected moments.

Axes:

```text
x-axis = strike
y-axis = implied volatility
line   = one timestamp snapshot
```

Use it to see the shape of the option curve and how it changes over time.

## Suggested Analysis Workflow

Use this process after every backtest:

```text
1. Overview
   Check total PnL, drawdown, and product contribution.

2. Product Scorecard
   Pick products with high PnL, negative PnL, or bad average mid edge.

3. Product Drilldown
   Inspect trades, PnL, position, spread, imbalance, z-score, and fill edge.

4. Options IV
   For VEV products, check whether losing strikes were rich/cheap.

5. Modify strategy
   Adjust edge thresholds, position limits, IV filters, or hedge behavior.
```

## Current R3 Takeaways

For the latest R3 run, the strategy has meaningful option PnL after lowering the option edge threshold.

Important observations:

```text
HYDROGEL_PACK remains the largest PnL contributor.
VEV_5100 and VEV_5200 are strong contributors.
VEV_5300 loses money and deserves investigation.
VELVETFRUIT_EXTRACT hedge PnL can be negative.
```

Suggested next strategy work:

```text
1. Add per-strike option thresholds instead of one global VEV edge.
2. Penalize or avoid VEV_5300 unless relative IV is strongly favorable.
3. Use relative IV as an entry filter.
4. Improve delta hedge so VELVETFRUIT_EXTRACT hedge does not bleed too much.
5. Test z-score windows 50/150/500 against entry timing.
```

## Applicability To Other Backtests

The dashboard works for any local `prosperity4bt` backtest log or `trader.py` run.

Generic sections:

```text
Overview
Product Drilldown
Top metrics
Position
Spread
Imbalance
Z-score
Execution edge
```

R3-specific section:

```text
Options IV
```

If the backtest does not contain `VELVETFRUIT_EXTRACT` and `VEV_*` products, the Options IV tab will show a message saying no VEV surface could be computed.

## Known Caveats

### Large HTML File

The R3 dashboard can be large because it embeds all Plotly charts.

If it feels slow, split output into separate pages:

```text
overview.html
product_drilldown.html
options_iv.html
```

### Local Backtester Fill Attribution

Book/tape fill attribution is inferred from local backtester trade records:

```text
empty counterparty = book fill
named counterparty = tape fill
```

This is useful locally, but may not perfectly match official exchange mechanics.

### Z-Score Windows Are Analysis Tools

The dashboard shows 50/150/500 tick z-scores. These are not guaranteed optimal.

Use them as diagnostics:

```text
50  = entry timing
150 = medium signal
500 = regime
```

Then validate any trading rule with backtests.
