"""Append-only publication ledger for forecast files.

This module owns publication.  The audit implementation is in ``audit.py`` and
does not import this module, the model, or the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any


class LedgerError(Exception):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _next_weekday(value: date) -> date:
    result = value + timedelta(days=1)
    while result.weekday() >= 5:
        result += timedelta(days=1)
    return result


def _source_digests(cache: Path | None) -> dict[str, str]:
    if cache is None or not cache.exists():
        return {}
    return {
        str(path.relative_to(cache)): sha256_file(path)
        for path in sorted(cache.rglob("*"))
        if path.is_file() and not path.name.endswith(".partial")
    }


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise LedgerError(f"cannot identify the code commit: {error}") from error


def _worktree_dirty() -> bool:
    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                ".",
                ":(exclude)ledger/**",
                ":(exclude)deploy/**",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise LedgerError(f"cannot identify uncommitted code: {error}") from error
    return bool(result.stdout.strip())


def _entries(ledger: Path) -> list[Path]:
    return (
        sorted(
            (
                path
                for path in ledger.iterdir()
                if path.is_dir() and path.name[:6].isdigit()
            ),
            key=lambda path: path.name,
        )
        if ledger.exists()
        else []
    )


def _stamp(manifest: Path, proof: Path, calendar: str | None) -> None:
    if calendar is not None:
        from opentimestamps.calendar import RemoteCalendar  # type: ignore[import-untyped]
        from opentimestamps.core.op import OpSHA256  # type: ignore[import-untyped]
        from opentimestamps.core.serialize import (  # type: ignore[import-untyped]
            BytesSerializationContext,
        )
        from opentimestamps.core.timestamp import (  # type: ignore[import-untyped]
            DetachedTimestampFile,
        )

        digest = OpSHA256()(manifest.read_bytes())
        timestamp = RemoteCalendar(calendar).submit(digest, timeout=20)
        detached = DetachedTimestampFile(OpSHA256(), timestamp)
        context = BytesSerializationContext()
        detached.serialize(context)
        proof.write_bytes(context.getbytes())
        return
    if shutil.which("ots") is None:
        raise LedgerError("the `ots` command is not installed")
    command = ["ots"]
    if calendar:
        command += ["-l", calendar]
    command += ["stamp"]
    if calendar:
        command += ["-c", calendar]
    command += ["-m", "1", str(manifest)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        raise LedgerError(f"OpenTimestamps stamp failed: {output}")
    generated = Path(f"{manifest}.ots")
    if generated.exists() and generated != proof:
        os.replace(generated, proof)
    if not proof.exists():
        raise LedgerError("OpenTimestamps returned without writing a proof")


def publish(
    scores: Path,
    ledger: Path,
    *,
    cache: Path | None = None,
    calendar: str | None = None,
    model_settings: dict[str, Any] | None = None,
    status: Path | None = None,
) -> Path:
    """Publish the exact bytes in ``scores`` as one chained ledger entry."""
    from index_core.audit import audit

    try:
        report = json.loads(scores.read_bytes())
        as_of = date.fromisoformat(report["as_of"])
        universe = str(report["universe"])
        horizon = int(report["horizon_days"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise LedgerError(f"scores is not a forecast report: {error}") from error

    horizon_start = _next_weekday(as_of)
    publication_date = datetime.now(UTC).date()
    if publication_date >= horizon_start:
        raise LedgerError(
            f"publication date {publication_date.isoformat()} is on or after "
            f"horizon start {horizon_start.isoformat()}"
        )

    if ledger.exists():
        existing = audit(ledger)
        if not existing.ok:
            raise LedgerError("ledger audit failed; refusing to publish")

    entries = _entries(ledger)
    if any(
        json.loads((entry / "manifest.json").read_text())["as_of"] == as_of.isoformat()
        for entry in entries
    ):
        raise LedgerError(f"an entry for as_of {as_of} already exists")
    previous = sha256_file(entries[-1] / "manifest.json") if entries else None
    sequence = len(entries) + 1
    entry_name = f"{sequence:06d}-{as_of.isoformat()}"
    ledger.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix=f".{entry_name}-", dir=ledger))
    try:
        scores_bytes = scores.read_bytes()
        (pending / "scores.json").write_bytes(scores_bytes)
        manifest = {
            "format": "equity-analysis-ledger-v1",
            "sequence": sequence,
            "as_of": as_of.isoformat(),
            "universe": universe,
            "horizon_days": horizon,
            "horizon_start": horizon_start.isoformat(),
            "code_commit": _commit(),
            "model_settings": model_settings or {},
            "scores_sha256": sha256_bytes(scores_bytes),
            "previous_manifest_sha256": previous,
            "source_response_sha256": _source_digests(cache),
            "timestamp_submitted_at": publication_date.isoformat(),
        }
        if not manifest["source_response_sha256"]:
            manifest["source_response_note"] = (
                "No source cache was supplied to the publisher; raw response "
                "digests are unavailable for this entry."
            )
        manifest["code_worktree_dirty"] = _worktree_dirty()
        manifest_path = pending / "manifest.json"
        manifest_path.write_bytes(_json_bytes(manifest))
        _stamp(manifest_path, pending / "manifest.ots", calendar)
        destination = ledger / entry_name
        os.replace(pending, destination)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise

    if status is not None:
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_bytes(_json_bytes(audit(ledger).status))
    return destination


def upgrade(ledger: Path, *, calendar: str | None = None) -> int:
    if shutil.which("ots") is None:
        raise LedgerError("the `ots` command is not installed")
    proofs = sorted(ledger.glob("[0-9][0-9][0-9][0-9][0-9][0-9]-*/manifest.ots"))
    if not proofs:
        return 0
    command = ["ots", "upgrade"]
    if calendar:
        command += ["-c", calendar]
    result = subprocess.run(
        [*command, *(str(path) for path in proofs)], capture_output=True, text=True
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        if "Pending confirmation" not in output and "not complete" not in output:
            raise LedgerError(f"OpenTimestamps upgrade failed: {output}")
    return len(proofs)
