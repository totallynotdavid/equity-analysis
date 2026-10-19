"""Independent grader for the published forecast ledger.

No model, pipeline, database, or report module is imported here. The grader
only consumes published JSON, ledger manifests, OTS proofs, and a price file
provided by the caller.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast


ENTRY = re.compile(r"^(\d{6})-(\d{4}-\d{2}-\d{2})$")
DEFAULT_BLOCK_TIME_SOURCE = "https://blockstream.info/api/block-height/{height}"


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    lines: tuple[str, ...]
    status: dict[str, Any]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entries(base: Path) -> list[Path]:
    if (base / "ledger").is_dir():
        base = base / "ledger"
    if not base.is_dir():
        raise FileNotFoundError(f"ledger path does not exist: {base}")
    return sorted(
        (path for path in base.iterdir() if path.is_dir() and ENTRY.match(path.name)),
        key=lambda path: path.name,
    )


def _fetch_bytes(source: str) -> bytes:
    with urllib.request.urlopen(source, timeout=20) as response:
        return cast("bytes", response.read())


def _remote_ledger(source: str) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    payload = _fetch_bytes(source)
    digest = hashlib.sha256(payload).hexdigest()
    temporary = tempfile.TemporaryDirectory(prefix="equity-ledger-audit-")
    root = Path(temporary.name)
    if zipfile.is_zipfile(io.BytesIO(payload)):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(root)
    else:
        bundle = json.loads(payload)
        files = bundle.get("files") if isinstance(bundle, dict) else None
        if not isinstance(files, dict):
            temporary.cleanup()
            raise ValueError("ledger URL must return a zip or a JSON files bundle")
        for name, value in files.items():
            relative = Path(str(name))
            if relative.is_absolute() or ".." in relative.parts:
                temporary.cleanup()
                raise ValueError(f"unsafe ledger URL file name: {name}")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(value, str) and value.startswith("base64:"):
                target.write_bytes(base64.b64decode(value.removeprefix("base64:")))
            elif isinstance(value, str):
                target.write_text(value)
            else:
                temporary.cleanup()
                raise ValueError(f"ledger URL file is not text: {name}")
    return temporary, root, digest


def _load_base(
    base: str | Path,
) -> tuple[tempfile.TemporaryDirectory[str] | None, Path, str | None]:
    value = str(base)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme in {"http", "https"}:
        temporary, root, digest = _remote_ledger(value)
        return temporary, root, digest
    return None, Path(value), None


def _proof_digest(manifest: Path, proof: Path) -> tuple[bool, Any | None]:
    if not proof.exists():
        return False, None
    try:
        from opentimestamps.core.serialize import (  # type: ignore[import-untyped]
            BytesDeserializationContext,
            DeserializationError,
        )
        from opentimestamps.core.timestamp import (  # type: ignore[import-untyped]
            DetachedTimestampFile,
        )

        detached = DetachedTimestampFile.deserialize(
            BytesDeserializationContext(proof.read_bytes())
        )
    except OSError, DeserializationError, ValueError, TypeError:
        return False, None
    return (
        bool(detached.file_digest == hashlib.sha256(manifest.read_bytes()).digest()),
        detached,
    )


def _block_time(source: str | Path, height: int) -> datetime:
    if isinstance(source, Path) or urllib.parse.urlparse(str(source)).scheme == "":
        raw = json.loads(Path(source).read_text())
        value = raw.get(str(height), raw.get(height)) if isinstance(raw, dict) else None
        if value is None:
            raise ValueError(f"block height {height} is absent from {source}")
        if isinstance(value, str) and len(value) == 64:
            raise ValueError(
                f"block-time source {source} returned a block hash, not a block time"
            )
    else:
        first = _fetch_bytes(str(source).format(height=height)).decode().strip()
        try:
            response = json.loads(first)
        except json.JSONDecodeError:
            response = None
        if isinstance(response, dict):
            value = response.get("timestamp", response.get("time"))
        else:
            value = first.strip('"')
        if value is not None and not isinstance(value, str):
            return _block_time_value(value)
        if value is None:
            value = first.strip('"')
        if str(source) == DEFAULT_BLOCK_TIME_SOURCE and len(value) == 64:
            value = json.loads(
                _fetch_bytes(f"https://blockstream.info/api/block/{value}")
            ).get("timestamp")
        elif len(value) == 64:
            raise ValueError(
                f"block-time source {source} returned a block hash, not a block time"
            )
    return _block_time_value(value)


def _block_time_value(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _attested_time(detached: Any, block_times: str | Path) -> tuple[date | None, bool]:
    """Return the earliest Bitcoin attestation date and whether one exists."""
    from opentimestamps.core.notary import (  # type: ignore[import-untyped]
        BitcoinBlockHeaderAttestation,
    )

    attestations = [
        attestation
        for _, attestation in detached.timestamp.all_attestations()
        if isinstance(attestation, BitcoinBlockHeaderAttestation)
    ]
    if not attestations:
        return None, False
    times = [
        _block_time(block_times, attestation.height) for attestation in attestations
    ]
    return min(value.date() for value in times), True


def _load_prices(path: Path | None) -> dict[str, dict[date, float]]:
    if path is None:
        return {}
    if path.suffix.lower() == ".csv":
        with path.open(newline="") as handle:
            rows = csv.DictReader(handle)
            result: dict[str, dict[date, float]] = {}
            for row in rows:
                ticker = row.get("ticker") or row.get("symbol")
                day = row.get("date")
                value = row.get("adj_close") or row.get("close") or row.get("price")
                if ticker and day and value:
                    result.setdefault(ticker.upper(), {})[date.fromisoformat(day)] = (
                        float(value)
                    )
            return result
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "prices" in raw:
        raw = raw["prices"]
    if not isinstance(raw, dict):
        raise ValueError("realised prices must be a JSON object or CSV")
    return {
        ticker.upper(): {
            date.fromisoformat(day): float(value) for day, value in values.items()
        }
        for ticker, values in raw.items()
    }


def _wilson(hits: int, total: int) -> tuple[float, float]:
    if total == 0:
        return math.nan, math.nan
    z = 1.96
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    radius = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    )
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and values[order[end]] == values[order[position]]:
            end += 1
        average = (position + end - 1) / 2 + 1
        for index in order[position:end]:
            ranks[index] = average
        position = end
    return ranks


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2:
        return None
    xr, yr = _rank(x), _rank(y)
    xm, ym = sum(xr) / len(xr), sum(yr) / len(yr)
    numerator = sum((a - xm) * (b - ym) for a, b in zip(xr, yr, strict=False))
    denominator = math.sqrt(
        sum((a - xm) ** 2 for a in xr) * sum((b - ym) ** 2 for b in yr)
    )
    return numerator / denominator if denominator else None


def _maturity_date(spy: dict[date, float], as_of: date, horizon: int) -> date | None:
    dates = sorted(day for day in spy if day > as_of)
    return dates[horizon - 1] if len(dates) >= horizon else None


def _weekday_lower_bound(as_of: date, horizon: int) -> date:
    """Return a weekday lower bound; exchange holidays can make it later."""
    result = as_of
    for _ in range(horizon):
        result += timedelta(days=1)
        while result.weekday() >= 5:
            result += timedelta(days=1)
    return result


def _track(
    entries: list[tuple[dict[str, Any], dict[str, Any]]], prices_path: Path | None
) -> tuple[str, dict[str, Any]]:
    prices = _load_prices(prices_path)
    matured: list[tuple[dict[str, Any], float]] = []
    first_dates: list[date] = []
    for manifest, report in entries:
        as_of = date.fromisoformat(manifest["as_of"])
        horizon = int(manifest["horizon_days"])
        end = _maturity_date(prices.get("SPY", {}), as_of, horizon)
        if end is None:
            first_dates.append(_weekday_lower_bound(as_of, horizon))
            continue
        first_dates.append(end)
        benchmark_initial = prices.get("SPY", {}).get(as_of)
        benchmark_end = prices.get("SPY", {}).get(end)
        if benchmark_initial is None or benchmark_end is None:
            continue
        benchmark_return = benchmark_end / benchmark_initial - 1
        for row in report.get("rows", []):
            ticker = str(row["ticker"]).upper()
            initial = prices.get(ticker, {}).get(as_of)
            final = prices.get(ticker, {}).get(end)
            if initial is None or final is None:
                continue
            matured.append((row, final / initial - 1 - benchmark_return))
    if not matured:
        first = min(first_dates).isoformat() if first_dates else "unknown"
        return f"PASS matured track record: none matured yet, not before {first}", {
            "matured": 0,
            "deciles": [],
            "rank_ic": None,
        }
    deciles = []
    for score in range(1, 11):
        values = [excess for row, excess in matured if int(row["score"]) == score]
        hits = sum(value > 0 for value in values)
        lo, hi = _wilson(hits, len(values))
        deciles.append(
            {
                "score": score,
                "rows": len(values),
                "hit_rate": hits / len(values) if values else None,
                "lo": None if math.isnan(lo) else lo,
                "hi": None if math.isnan(hi) else hi,
            }
        )
    ic = _spearman(
        [float(row.get("prob", row["score"])) for row, _ in matured],
        [value for _, value in matured],
    )
    return (
        f"PASS matured track record: {len(matured)} forecasts, rank IC {ic if ic is not None else 'none'}",
        {"matured": len(matured), "deciles": deciles, "rank_ic": ic},
    )


def audit(
    base: str | Path,
    prices: Path | None = None,
    block_times: str | Path = DEFAULT_BLOCK_TIME_SOURCE,
) -> AuditResult:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    lines: list[str] = []
    failures: list[str] = []
    pending_stamps = 0
    verified_stamps = 0
    try:
        temporary, root, remote_digest = _load_base(base)
        entries = _entries(root)
    except (OSError, ValueError, urllib.error.URLError) as error:
        return AuditResult(
            False,
            (f"FAIL missing ledger: {error}",),
            {
                "forecasts_committed": 0,
                "chain_verified_up_to": None,
                "earliest_stamp": None,
                "live_record": "no forecasts committed",
                "track_record": {"matured": 0, "deciles": [], "rank_ic": None},
            },
        )
    expected_previous: str | None = None
    loaded: list[tuple[dict[str, Any], dict[str, Any]]] = []
    earliest_stamp: date | None = None
    try:
        for position, entry in enumerate(entries, 1):
            match = ENTRY.match(entry.name)
            assert match is not None
            sequence, name_date = int(match[1]), match[2]
            if sequence > position:
                failures.append(
                    f"FAIL missing entry: sequence {position:06d} is absent before {entry.name}"
                )
            elif sequence < position:
                failures.append(
                    f"FAIL reordered entry: {entry.name} has sequence {sequence}, expected {position}"
                )
            manifest_path, scores_path, proof_path = (
                entry / "manifest.json",
                entry / "scores.json",
                entry / "manifest.ots",
            )
            for path in (scores_path, manifest_path, proof_path):
                if not path.exists():
                    failures.append(f"FAIL missing entry: {path}")
            try:
                manifest = json.loads(manifest_path.read_text())
                report = json.loads(scores_path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                failures.append(
                    f"FAIL edited bytes: {entry.name}: cannot read entry ({error})"
                )
                continue
            if manifest.get("as_of") != name_date:
                failures.append(
                    f"FAIL reordered entry: {entry.name} names {manifest.get('as_of')}"
                )
            if manifest.get("sequence") != position:
                failures.append(
                    f"FAIL reordered entry: {entry.name} manifest sequence {manifest.get('sequence')}, expected {position}"
                )
            if manifest.get("previous_manifest_sha256") != expected_previous:
                failures.append(
                    f"FAIL chain: {entry.name} expected previous {expected_previous}, got {manifest.get('previous_manifest_sha256')}"
                )
            source_digests = manifest.get("source_response_sha256")
            source_note = manifest.get("source_response_note")
            if not isinstance(source_digests, dict):
                failures.append(
                    f"FAIL provenance: {entry.name} source_response_sha256 is not a map"
                )
            elif not source_digests and not (
                isinstance(source_note, str) and source_note.strip()
            ):
                failures.append(
                    f"FAIL provenance: {entry.name} has no source response digests or explanation"
                )
            if scores_path.exists():
                actual = _digest(scores_path)
                if actual != manifest.get("scores_sha256"):
                    failures.append(
                        f"FAIL edited bytes: {scores_path} expected {manifest.get('scores_sha256')}, got {actual}"
                    )
            valid_proof, detached = _proof_digest(manifest_path, proof_path)
            if not valid_proof:
                failures.append(f"FAIL stamp: {proof_path} does not verify")
            elif detached is not None:
                try:
                    attested, has_bitcoin = _attested_time(detached, block_times)
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    urllib.error.URLError,
                ) as error:
                    failures.append(
                        f"FAIL stamp: {proof_path} block time unavailable ({error})"
                    )
                else:
                    if not has_bitcoin:
                        pending_stamps += 1
                    else:
                        if attested is not None and (
                            earliest_stamp is None or attested < earliest_stamp
                        ):
                            earliest_stamp = attested
                        if attested is not None and attested >= date.fromisoformat(
                            manifest["horizon_start"]
                        ):
                            failures.append(
                                f"FAIL stamp after horizon: {entry.name} attested {attested}, horizon starts {manifest['horizon_start']}"
                            )
                        else:
                            verified_stamps += 1
            expected_previous = _digest(manifest_path)
            loaded.append((manifest, report))
    finally:
        if temporary is not None:
            temporary.cleanup()
    if remote_digest is not None:
        lines.append(f"PASS remote ledger bytes received: {remote_digest}")
    if failures:
        lines.extend(failures)
    elif entries:
        lines.append(f"PASS chain is unbroken: {len(entries)} entries")
        lines.append(
            f"PASS every file matches its hash: {len(entries)} scores and manifests"
        )
    else:
        lines.append("PASS chain is unbroken: no forecasts committed")
        lines.append("PASS every file matches its hash: 0 files")
    if pending_stamps:
        lines.append(
            f"PENDING timestamps: {pending_stamps} proof(s) have no Bitcoin attestation"
        )
    elif not any(line.startswith("FAIL stamp") for line in failures):
        lines.append(
            f"PASS timestamps before forecast horizon: {verified_stamps} stamps"
        )
    if entries:
        track_line, track = _track(loaded, prices)
    else:
        track_line, track = (
            "PASS matured track record: no forecasts committed",
            {"matured": 0, "deciles": [], "rank_ic": None},
        )
    lines.append(track_line)
    status = {
        "forecasts_committed": len(entries),
        "chain_verified_up_to": loaded[-1][0]["as_of"]
        if loaded and not failures
        else None,
        "earliest_stamp": earliest_stamp.isoformat() if earliest_stamp else None,
        "live_record": track_line.removeprefix("PASS matured track record: "),
        "track_record": track,
    }
    return AuditResult(not failures, tuple(lines), status)
