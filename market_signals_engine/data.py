import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

from .models import Bar


REQUIRED_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume")


def load_bars(path: Path) -> dict[str, list[Bar]]:
    if not path.is_file():
        raise FileNotFoundError(f"daily bars CSV not found: {path}")

    grouped: dict[str, list[Bar]] = defaultdict(list)
    seen: set[tuple[str, date]] = set()
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: CSV is empty")
        columns = {name.strip().lower() for name in reader.fieldnames}
        missing = set(REQUIRED_COLUMNS) - columns
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(sorted(missing))}")

        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{path}:{line_number}: row contains extra CSV fields")
            normalized = {key.strip().lower(): value for key, value in row.items() if key}
            try:
                if any(normalized.get(name) is None for name in REQUIRED_COLUMNS):
                    raise ValueError("row has missing columns or values")
                symbol = normalized["symbol"].strip().upper()
                bar_date = date.fromisoformat(normalized["date"].strip())
                prices = {
                    name: float(normalized[name])
                    for name in ("open", "high", "low", "close", "volume")
                }
                bar = Bar(symbol=symbol, date=bar_date, **prices)
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line_number}: invalid daily bar: {error}") from error

            key = (bar.symbol, bar.date)
            if key in seen:
                raise ValueError(f"{path}:{line_number}: duplicate bar for {bar.symbol} on {bar.date}")
            seen.add(key)
            grouped[bar.symbol].append(bar)

    if not grouped:
        raise ValueError(f"{path}: no daily bars found")

    for bars in grouped.values():
        bars.sort(key=lambda bar: bar.date)
    return dict(grouped)
