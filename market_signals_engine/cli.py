import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .data import load_bars
from .strategy import evaluate_symbol


def generate_signals(input_path: Path, output_dir: Path) -> Path:
    grouped_bars = load_bars(input_path)
    signals = [
        evaluate_symbol(bars).to_dict()
        for _, bars in sorted(grouped_bars.items())
    ]
    latest_date = max(signal["as_of_date"] for signal in signals)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": input_path.name,
        "signals": signals,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"signals_{latest_date}.json"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            delete=False,
            newline="\n",
        ) as output:
            temporary_path = Path(output.name)
            json.dump(payload, output, indent=2, allow_nan=False)
            output.write("\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline daily market signal proposals.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(os.environ.get("MARKET_DATA_FILE", "data/input/daily_bars.csv")),
        help="CSV file containing daily OHLCV bars",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("SIGNALS_OUTPUT_DIR", "data/output")),
        help="directory for generated JSON signal files",
    )
    args = parser.parse_args()
    print(generate_signals(args.input, args.output_dir))


if __name__ == "__main__":
    main()
