"""Turns SMV API rows into store rows: ISO dates, short column names, no envelope."""

from datetime import datetime
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from bvl.smv import Record

Row = dict[str, Any]


def iso_date(value: str | None) -> str | None:
    """`07/11/2025` or `07/11/2025 12:00:00 a.m.` as `2025-11-07`."""
    if not value:
        return None
    return datetime.strptime(value[:10], "%d/%m/%Y").date().isoformat()


def iso_minute(value: str | None) -> str | None:
    """`31/10/2026 00:19` as `2026-10-31T00:19`."""
    if not value:
        return None
    return datetime.strptime(value[:16], "%d/%m/%Y %H:%M").isoformat(timespec="minutes")


def quote(record: Record) -> Row:
    return {
        "date": iso_date(record["FechaCotizacion"]),
        "ticker": record["Valor"].strip(),
        "name": (record.get("Descripcion") or "").strip(),
        "prev_date": iso_date(record.get("FechaAnterior")),
        "prev_close": record.get("Cierre_Anterior"),
        "open": record.get("Apertura_Actual"),
        "high": record.get("Maxima_Actual"),
        "low": record.get("Minima_Actual"),
        "close": record.get("Cierre_Actual"),
        "average": record.get("Promedio_Actual"),
        "amount": record.get("Monto_Negociado"),
    }


def index_level(record: Record) -> Row:
    return {
        "date": iso_date(record["Fecha"]),
        "code": record["Tipo"].strip(),
        "name": (record.get("Indice") or "").strip(),
        "prev_close": record.get("anterior"),
        "open": record.get("apertura"),
        "high": record.get("maxima"),
        "low": record.get("minima"),
        "close": record.get("ultimo"),
    }


def event(record: Record) -> Row:
    return {
        "filing": record["Numero_Expediente"].strip(),
        "ruc": (record.get("RUC") or "").strip(),
        "rpj": (record.get("RPJ") or "").strip(),
        "company": (record.get("Empresa") or "").strip(),
        "kind": (record.get("Tipo_Hecho") or "").strip(),
        "agreed_on": iso_date(record.get("Fecha_Acuerdo_Hecho")),
        "filed_at": iso_minute(record.get("Fecha_Presentacion")),
        "description": (record.get("Descripcion_Hecho") or "").strip(),
        "sector": (record.get("Sector") or "").strip(),
        "subsector": (record.get("SubSector") or "").strip(),
        "company_type": (record.get("Tipo_Empresa") or "").strip(),
    }
