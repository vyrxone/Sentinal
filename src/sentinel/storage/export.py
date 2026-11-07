"""Export audit events to JSON or CSV (atomic write, spreadsheet-safe CSV)."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .. import APP_NAME, __version__
from .events_store import Event

CSV_COLUMNS = ["id", "time_utc", "category", "event_type", "severity", "path", "message", "details"]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> str:
    """Neutralise spreadsheet formula injection (file names are attacker-controllable)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def export_events(events: Iterable[Event], dest: str | Path, fmt: str | None = None) -> Path:
    dest = Path(dest)
    fmt = (fmt or dest.suffix.lstrip(".")).lower()
    if fmt not in ("json", "csv"):
        raise ValueError("Export format must be 'json' or 'csv'.")
    rows = [e.as_dict() for e in events]
    tmp = dest.with_name(dest.name + ".tmp")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if fmt == "json":
            payload = {
                "app": APP_NAME,
                "version": __version__,
                "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "count": len(rows),
                "events": rows,
            }
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            with open(tmp, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(CSV_COLUMNS)
                for row in rows:
                    details = json.dumps(row["details"], ensure_ascii=False) if row["details"] else ""
                    writer.writerow([csv_safe(row["id"]), row["time_utc"], row["category"],
                                     row["event_type"], row["severity"], csv_safe(row["path"]),
                                     csv_safe(row["message"]), csv_safe(details)])
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest


def export_events_streamed(batches: Iterable[list], dest: str | Path, fmt: str | None = None, *, cancel=None,
                           progress=None, total: int | None = None) -> int:
    """Stream events (batches from ``EventStore.iter_batches``) to JSON or CSV with bounded memory.

    Atomic (temp file then rename), formula-injection safe, cancellable. Returns the row count, or
    raises ``ExportCancelled`` after removing the partial file.
    """
    dest = Path(dest)
    fmt = (fmt or dest.suffix.lstrip(".")).lower()
    if fmt not in ("json", "csv"):
        raise ValueError("Export format must be 'json' or 'csv'.")
    tmp = dest.with_name(dest.name + ".tmp")
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with open(tmp, "w", newline="" if fmt == "csv" else None,
                  encoding="utf-8-sig" if fmt == "csv" else "utf-8") as fh:
            writer = None
            if fmt == "json":
                head = {"app": APP_NAME, "version": __version__,
                        "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                fh.write(json.dumps(head, ensure_ascii=False)[:-1] + ', "events": [')
            else:
                writer = csv.writer(fh)
                writer.writerow(CSV_COLUMNS)
            for batch in batches:
                if cancel is not None and cancel.is_set():
                    raise ExportCancelled()
                for e in batch:
                    row = e.as_dict()
                    if fmt == "json":
                        fh.write(("," if count else "") + "\n" + json.dumps(row, ensure_ascii=False))
                    else:
                        details = json.dumps(row["details"], ensure_ascii=False) if row["details"] else ""
                        writer.writerow([csv_safe(row["id"]), row["time_utc"], row["category"], row["event_type"],
                                         row["severity"], csv_safe(row["path"]), csv_safe(row["message"]),
                                         csv_safe(details)])
                    count += 1
                if progress:
                    progress(count, total or count, "")
            if fmt == "json":
                fh.write("\n], \"count\": %d}\n" % count)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return count


class ExportCancelled(Exception):
    pass
