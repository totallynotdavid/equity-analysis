# Forecast ledger

The ledger publishes the exact bytes of each forecast before its 63-trading-day
outcome is known. A publication is one directory under `ledger/`:

The repository starts with an empty `ledger/` directory. The first real
`deploy:data` run creates the first entry; no seed forecast is committed.
Each publication is one directory under `ledger/`:

```text
ledger/000001-<as_of>/
├── manifest.json
├── manifest.ots
└── scores.json
```

`scores.json` is copied byte for byte. `manifest.json` records its SHA-256,
the previous manifest's SHA-256, the forecast date, universe, horizon, code
commit, model settings, and SHA-256 digests of raw response files in the source
cache. If no cache was supplied, it records a `source_response_note` explaining
why the digest map is empty. `manifest.ots` is an OpenTimestamps detached proof
of the manifest bytes. The sequence and previous digest make the directories an
append-only chain.

## Publish

`mise run deploy:data` audits the existing ledger, scores the real demo
universe, and appends one entry:

```bash
mise run deploy:data
```

The task needs the real-data credentials described in [CLI](CLI.md#real-data).
It does not schedule a recurring run. To use a test calendar, pass its URL to
the publish command:

```bash
uv run eq ledger publish \
  --scores deploy/scores.json \
  --ledger ledger \
  --cache data/cache \
  --calendar https://calendar.example/
```

Pending proofs can be upgraded later. A pending proof does not stop a publish
or an audit. The audit prints its count and checks the time after a Bitcoin
attestation arrives:

```bash
uv run eq ledger upgrade --ledger ledger
```

The publisher refuses a duplicate `as_of` and refuses to append when the
existing chain does not audit. It writes a temporary directory and renames it
only after the score bytes, manifest, and timestamp proof exist.

## Audit

Anyone with the ledger can check it without the SQLite database or model code:

```bash
uv run eq audit ledger
```

The grader checks the chain, the exact score bytes, the detached proof, and
that an externally attested time precedes each forecast horizon. A pending OTS
proof is reported as `PENDING`, not as a publisher-declared date. For a matured
proof, the grader reads the Bitcoin block header time from the default
Blockstream source. Use `--block-times URL_OR_PATH` to provide a JSON map from
block height to ISO time or Unix time, including a private or test source.

The argument may be a local ledger path or a URL returning a zip ledger bundle.
The URL response is hashed before the bundle is graded:

```bash
uv run eq audit https://example.test/ledger.zip
```

Realised prices are a separate input. A CSV has `date,ticker,adj_close` (or
`close`), and a JSON file maps tickers to date-to-price objects:

```bash
uv run eq audit ledger --prices realised.csv
```

For every matured forecast, the grader waits for 63 supplied SPY trading dates
after `as_of`, then reports the return window from `as_of` through the 63rd
date. It reports hit rate by score decile, a Spearman rank IC, and Wilson 95%
intervals. It uses SPY from the supplied prices, so a third party can grade the
published bytes with its own price source.

Without `--prices`, the audit reports `none matured yet, not before <date>`.
That date is 63 weekdays after the earliest `as_of`, so it is a lower bound:
market holidays can push the actual first maturity later.

Tampering is an error, not a repair operation. Changing a probability prints
the expected and actual score-file hashes. Removing or swapping an entry names
the missing or reordered sequence. A timestamp after `horizon_start` names the
two dates that disagree.

The repository does not claim a seed entry came from `deploy:data`. The first
real run is made by the operator when the Tiingo and SEC sources are available.
Until then, an empty ledger audits as a passing chain with `no forecasts
committed`; the evidence page shows the same empty status. A later real-data
publish will include the source-cache digests and generate
`deploy/ledger-status.json` from the ledger. It does not contain hand-entered
counts.

An empty ledger produces these audit lines:

```text
PASS chain is unbroken: no forecasts committed
PASS every file matches its hash: 0 files
PASS timestamps before forecast horizon: 0 stamps
PASS matured track record: no forecasts committed
```
