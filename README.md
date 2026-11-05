# Albridge

**Cross-venue liquidity translation for Antivist Markets.**

Albridge is an experimental market-making bridge that reads sports prediction-market order books from **Pascal** and **Polymarket**, derives hedgeable quotes for a corresponding **Antivist** perpetual market, and publishes **theoretical Antivist quote ladders** locally. An optional authenticated testnet mode can submit orders using the public Python SDK.

The core idea is to make liquidity from separate, event-based prediction markets accessible through a single perpetual order book. Albridge does not copy prices between exchanges: it converts external outcome prices and available depth into Antivist quote prices, quantities, and explicit hedge legs.

> **Status: hackathon proof of concept.** The default reviewer workflow is a **public, no-account, no-order** demonstration. Antivist testnet trading is an optional separate path. External hedge execution after a fill, complete inventory accounting, and low-latency production execution are **not implemented**. Do not use this project to manage real capital.

## How it works

```text
                  Public Antivist metadata
                  (market + next fixture)
                           |
                     Fixture resolver
                     /              \
              Pascal markets     Polymarket markets
                    |                   |
                WebSocket           WebSocket
                order books         order books
                    \                   /
                     Market snapshots
                           |
                 Outcome ask depth + fees
                           |
                 Hedgeable quote ladders
                           |
               Market rules + risk budgets
                           |
                   DesiredOrder set
                           |
               Theoretical Antivist book
                (read-only local viewer)
                           |
                [OPTIONAL TESTNET MODE]
               SDK reconciliation and
                   order execution
```

Antivist public market metadata (or an explicitly supplied export of it) is the reference source for the team market, upcoming fixture, pricing anchors, tick size, and quantity constraints. Pascal and Polymarket are resolved **independently** using the fixture's teams and kickoff time. A missing venue does not prevent discovery of the other.

Market data is received on separate WebSocket threads. **Public Demo mode never initializes an Antivist trading account, reads private orders, reconciles, or submits/cancels orders.** In the optional authenticated mode, REST telemetry and LIVE reconciliation are synchronous; this is **not** a latency-optimized execution engine.

## Pricing and hedge construction

Antivist markets are perpetual instruments with prices discovered through orders and capital on the exchange, not through embedded external probability estimates. Albridge uses the market's outcome-dependent anchors to construct *external hedge-based quotes*; these calculations do not determine Antivist's market price.

Let:

- `W`, `D`, `L` be the Antivist WIN, DRAW, and LOSS outcome anchors, with `L < D < W`.
- `p_X` be the executable **ask** price for external outcome `X` at a particular order-book level.
- `f_X` be the corresponding fee rate. The implemented fee-adjusted unit cost is `c_X = p_X + f_X * p_X * (1 - p_X)`.

For a **SELL** quote on Antivist, Albridge constructs a hedge from external **WIN + DRAW** asks:

```text
SELL_quote = L + (W - L) * c_WIN + (D - L) * c_DRAW

Hedge quantities per 1 Antivist unit:
    WIN  = W - L
    DRAW = D - L
```

For a **BUY** quote on Antivist, Albridge constructs a hedge from external **DRAW + LOSS** asks:

```text
BUY_quote = W - (W - D) * c_DRAW - (W - L) * c_LOSS

Hedge quantities per 1 Antivist unit:
    DRAW = W - D
    LOSS = W - L
```

These formulas describe the implemented theoretical quote construction, **not guaranteed executable profits**. Fees use the supported venue fee model; unsupported fee configurations are rejected instead of silently approximated.

For each quote side and venue, the ladder builder walks the relevant external **ask** books from the cheapest available liquidity outward. At every step it derives the maximum Antivist quantity that could be hedged by **all required legs**, consumes that depth virtually, and proceeds to the next limiting level. Every resulting order retains its venue and required hedge legs.

The current risk-allocation path splits the available **DRAW ask** quantity 50/50 between BUY- and SELL-side theoretical ladders before sizing them. This reduces double-counting of the same DRAW depth across opposite quote sides; it is **not an external liquidity reservation**. Quotes from different venues remain separate orders even if their prices coincide. A consolidated viewer can nevertheless display a tighter effective spread by combining their best levels.

The final quote set is normalized to Antivist market rules and constrained by configured order and total desired-quote notional budgets. These budgets are **not** a substitute for portfolio-level maximum-loss controls.

## Supported workflow

| Component | Current behavior |
| --- | --- |
| Fixture discovery | Reads the next fixture from an Antivist team market |
| Venue discovery | Independently matches supported Pascal and Polymarket soccer outcome markets |
| Market data | Separate WebSocket order books and health checks |
| Quote construction | Venue-specific BUY/SELL hedge ladders with quantities and leg composition |
| Risk-aware quoting | Tick/quantity normalization, notional limits, and shared DRAW depth allocation |
| Public demonstration | Live external books transformed to local, theoretical Antivist quote ladders; no trading credentials |
| Optional Antivist execution | Testnet order submission, cancellation, reconciliation, and local order registry |
| Monitoring | Order-book, pricing, and MM-control terminal views |
| Unhedged-fill protection | Detects relevant recent Antivist executions and blocks normal quoting/cancels managed orders where possible |
| External PM execution | **Not implemented** |
| Full P&L / inventory rollover | **Not implemented** |

## Requirements

- Python **3.11+** and Git.
- Network access to Antivist testnet, Pascal market data, and Polymarket market data.
- **No Antivist API key or trading account is needed for Public Demo mode.** Credentials are needed only for optional authenticated testnet mode.
- At least one compatible, currently discoverable external market for the chosen Antivist fixture. External market availability is not guaranteed.

Authenticated exchange operations use only the public `antivist-python` SDK. The public, read-only demo may retrieve unauthenticated market metadata over HTTPS. Albridge has no Django model imports, direct database access to the exchange, or privileged matching-engine interfaces.

## Installation

```bash
git clone https://github.com/antivist-xyz/albridge.git
cd albridge

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The package declares the official SDK from [`antivist-xyz/antivist-python`](https://github.com/antivist-xyz/antivist-python). This requires `git` to be available during installation. The package is installed in editable mode for development; external reviewers can also use `python -m pip install .`.

Verify the environment:

```bash
python -m pip check
python -c "from antivist.client import AntivistClient; import albridge; print('SDK IMPORT OK')"
python -m pytest -q
```

**Validation:** the project passed **178 tests** in the development environment and in a fresh virtual environment on November 5, 2026. This is not a claim of live execution or production safety coverage.

## Public Demo — no Antivist account required

This is the **recommended workflow for external reviewers and the hackathon**. It shows real Pascal/Polymarket WebSocket market data translated into **theoretical** Antivist BUY/SELL quotes. It **never submits or cancels an Antivist order**.

The CLI obtains *public market metadata* (team, next fixture, anchors, tick/quantity rules) from an unauthenticated Antivist endpoint. These reference parameters do **not** represent the live Antivist order book. An Antivist trading account is not needed.

```bash
# From the repository root, after installation:
ALBRIDGE_EXECUTE_LIVE=0 ALBRIDGE_TESTNET_SMOKE=0 \
  python -m albridge.main --public-demo --market manchester-city
```

Open a second terminal:

```bash
python -m albridge.book_view
```

The top banner identifies **PUBLIC DEMO — theoretical quotes only**. All quotes displayed are computed locally; none are resting orders on Antivist. No external hedge contracts are purchased either.

**Venue availability depends on a matching current fixture on Pascal and/or Polymarket.** An absent venue is reported as unavailable rather than filled with fake liquidity. The market slug is the Antivist *team market*, not a static external contract identifier.

### When public metadata cannot be fetched

If the public Antivist endpoint is blocked, unavailable, or requires authentication, supply a local JSON export **of publicly shareable Antivist market metadata** instead:

```bash
ALBRIDGE_EXECUTE_LIVE=0 ALBRIDGE_TESTNET_SMOKE=0 \
  python -m albridge.main --public-demo --market manchester-city \
    --market-config ./market-public.json
```

The JSON may be either the market object or an envelope `{"market": {...}}`. Required fields are `id`, `team` (`id`, `slug`, `name`, optional `aliases`), `next_fixture` (`fixture_label`, UTC `kickoff_at`, `oracle_anchor`, `pf_win`, `pf_draw`, `pf_loss`), `tick_size`, `quantity_step`, and `min_quantity`. Values must be **real data from the selected market**; do not invent anchors or claim a historical export reflects current Antivist state. Never include keys, secrets, account details, or private API responses in that file.

`--public-demo` refuses to start if `ALBRIDGE_EXECUTE_LIVE=1` or `ALBRIDGE_TESTNET_SMOKE=1` is present in the environment. Both must be set to `0`. **Public Demo does not perform private order telemetry or live order reconciliation.**

### Viewer-only visual mode

For a recording, you may relax visual freshness warnings **only in the viewer process**:

```bash
ALBRIDGE_VIDEO_MODE=1 python -m albridge.book_view
```

This does not modify the runtime's feed-health checks or make outdated liquidity safe to trade.

## Optional authenticated testnet mode

This path is **not required for the Pascal hackathon review**. It requires a valid Antivist testnet account and is intended for controlled operator testing only.

```bash
cp .env.example .env
# Insert your personal TESTNET key and secret into .env.
# Never commit .env.
```

```dotenv
ANTIVIST_BASE_URL=https://testnet.antivist.xyz
ANTIVIST_API_KEY=your_testnet_key
ANTIVIST_API_SECRET=your_testnet_secret
ANTIVIST_MARKET_SLUG=manchester-city
ALBRIDGE_EXECUTE_LIVE=0
ALBRIDGE_TESTNET_SMOKE=0
ALBRIDGE_MAX_TOTAL_NOTIONAL=20000
ALBRIDGE_MAX_ORDER_NOTIONAL=2500
ALBRIDGE_LEVELS_PER_SIDE=5
ALBRIDGE_LEVEL_SIZE_DECAY=0.80
```

Authenticated DRY RUN (no normal LIVE reconciliation, but it does use account APIs):

```bash
python -m albridge.main --market manchester-city
```

Controlled LIVE testnet execution (submits/cancels orders):

```bash
ALBRIDGE_EXECUTE_LIVE=1 ALBRIDGE_TESTNET_SMOKE=0 \
  python -m albridge.main --market manchester-city
```

An independent `ALBRIDGE_TESTNET_SMOKE=1` can also submit an order without LIVE; keep it disabled unless expressly testing. `ALBRIDGE_EXECUTE_LIVE=0` alone is not a blanket no-trading guarantee for the authenticated path.

The operational monitor is available through `python -m albridge.operations_tui`, but in Public Demo **account-level risk and exchange-order data are unavailable by design**, rather than zero.

Authenticated LIVE mode relies on local registry state and protective shutdown on ordinary `Ctrl+C`. OS crashes, connectivity failures, and unknown account state can leave orders live. Always verify open orders through the exchange. External hedging is not implemented; LIVE mode does not protect against trading losses.

## Project layout

```text
src/albridge/
  main.py                 CLI entry point (public and authenticated modes)
  public_market.py        Credential-free market metadata validation and retrieval
  runtime.py              Market-data, pricing and execution loop
  market_resolver.py      Independent venue discovery from Antivist fixture
  venues/                 Pascal and Polymarket resolvers, WS clients, books
  hedge_pricing.py        Theoretical hedge/fair-entry calculations
  hedge_ladder.py         Depth-aware hedgeable quote ladders
  ladder_normalizer.py    Antivist tick and size compliance
  quote_budget.py         Per-level and aggregate desired-quote sizing
  order_manager.py        Desired orders and reconciliation decisions
  execution.py            Public-SDK submit/cancel and LIVE reconciliation
  order_registry.py       Persistent local managed-order state
  shutdown.py             Protective cancellation and verification
  risk/                   Allocation, feed/execution guards, limits, registries
  depth_publisher.py      Read-only depth snapshot output
  operations_snapshot.py  Read-only operational telemetry output
  book_view.py            Market-depth/pricing terminal
  operations_tui.py       Market-maker operations terminal
scripts/                   Discovery, inspection and operator utilities
tests/                     Unit and deterministic integration-style tests
```

## Known limitations

1. **No external hedge execution.** Albridge calculates theoretical PM hedge legs but does not buy the corresponding contracts after an Antivist fill. Antivist positions can therefore become unhedged.
2. **Limited execution-history coverage.** The fill guard examines API-returned recent executions; it does not persist an exhaustive execution cursor or prove complete historical coverage.
3. **Synchronous REST in the main runtime.** Antivist telemetry and LIVE reconciliation can block pricing and snapshot publication for seconds. This prototype is not designed for low-latency or high-frequency operation.
4. **Liquidity is not reserved.** Published external depth can change, disappear, or be consumed elsewhere before a hedge can execute. Spread and calculated capacity do not imply realizable arbitrage.
5. **Partial inventory accounting.** Full hedge P&L, unmatched exposure, event settlement, and perpetual-position rollover are not yet managed end to end.
6. **Limited scope.** The current resolvers target supported soccer fixtures and three-way WIN/DRAW/LOSS outcome markets; incompatible or ambiguous external markets are skipped.
7. **Local operational state.** SQLite and snapshot files live under `data/` and `recordings/`. They are local artifacts, not durable distributed coordination or exchange-side guarantees.

## Development

Run the tests:

```bash
python -m pytest -q
```

The test suite includes credential-free public-metadata and safety-mode checks, as well as core book transformations, fixture and venue discovery, hedge construction, normalization, quote budgets, order reconciliation, execution response validation, and selected risk/registry behaviors. It does **not** constitute end-to-end validation of production trading or external hedging.

Contributions should preserve the separation between the Antivist **public API client**, venue adapters, quote construction, and execution/risk logic. Do not introduce private Antivist backend access into Albridge.

## Project status and affiliation

Albridge is an **Antivist Markets prototype** demonstrating cross-venue liquidity translation with Pascal and Polymarket market data. It is not an official Pascal or Polymarket product and does not imply endorsement by either venue.

**Experimental software. Testnet use only. No warranty of executable pricing, hedge availability, or loss protection.**