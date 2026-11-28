"""Manual offline import: python -m scripts.import_market_snapshot INPUT OUTPUT --retrieved-at ISO_TIMESTAMP."""
import argparse
import json
from pathlib import Path
from market_data import SpainEurostatHPI, write_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--retrieved-at", required=True)
    args = parser.parse_args()
    series = SpainEurostatHPI().parse(
        json.loads(args.input.read_text(encoding="utf-8")), args.retrieved_at)
    write_snapshot(args.output, [series])


if __name__ == "__main__":
    main()
