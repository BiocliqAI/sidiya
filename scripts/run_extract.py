from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.pipeline import run_extraction


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract discharge PDF into structured JSON")
    parser.add_argument("--pdf", required=True, help="Path to scanned discharge PDF")
    parser.add_argument("--out", required=True, help="Path to write JSON output")
    parser.add_argument("--ocr-model", default=None, help="Landing.ai parse model")
    parser.add_argument("--json-model", default=None, help="Gemini model for structured extraction step")
    parser.add_argument("--gemini-api-key", default=None, help="Optional Gemini API key override")
    parser.add_argument("--landing-api-key", default=None, help="Optional Landing.ai API key override")
    args = parser.parse_args()

    result = run_extraction(
        args.pdf,
        model_ocr=args.ocr_model,
        model_json=args.json_model,
        api_key=args.gemini_api_key,
        landing_api_key=args.landing_api_key,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote extraction JSON to {out_path}")


if __name__ == "__main__":
    main()
