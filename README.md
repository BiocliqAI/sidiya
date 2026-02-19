# Oyster Discharge -> 90-Day Care Plan Extractor (CHF-first)

Scaffold app to:
1. Ingest scanned discharge summary PDF
2. Run Landing.ai OCR/parse extraction
3. Use Gemini to convert parsed text to structured JSON (CHF-aware)
4. Validate against JSON Schema

## Project Layout

- `app/main.py` FastAPI app (`/`, `/health`, `/extract`)
- `app/pipeline.py` 2-stage extraction pipeline
- `app/gemini_client.py` Gemini REST client
- `schemas/discharge_chf_output.schema.json` output contract
- `scripts/run_extract.py` CLI runner
- `templates/index.html` visual dashboard
- `templates/care_plan.html` simplified post-discharge care plan page
- `static/app.js` and `static/app.css` frontend logic and styles
- `static/care_plan.js` care plan rendering logic

## Setup

```bash
cd /Users/rengarajanbashyam/Desktop/Oyster
pyenv local 3.12.8
python -m venv .venv312
source .venv312/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set GEMINI_API_KEY + LANDINGAI_API_KEY
```

## Web App Usage

```bash
source .venv312/bin/activate
uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000` in your browser.
After running extraction, open `http://127.0.0.1:8000/care-plan` for patient-facing guide view.

## CLI Usage

```bash
source .venv312/bin/activate
python scripts/run_extract.py \
  --pdf /absolute/path/discharge.pdf \
  --out /Users/rengarajanbashyam/Desktop/Oyster/output/extraction.json \
  --landing-api-key "<your-landing-key>" \
  --gemini-api-key "<your-gemini-key>"
```

## API Usage

```bash
source .venv312/bin/activate
uvicorn app.main:app --reload --port 8000
```

```bash
curl -X POST "http://127.0.0.1:8000/extract" \
  -F "pdf=@/absolute/path/discharge.pdf"
```

## Notes

- Current scaffold is CHF-first and expects `clinical_modules.chf.enabled=true`.
- Childbirth/postpartum care logic is intentionally out-of-scope.
- Schema validation fails fast if model output is incomplete/wrongly shaped.
- OCR source is Landing.ai Parse API.
- Parser uses a hybrid method: deterministic markdown extraction (diagnosis, admission reason, follow-up doctor/date, meds, advice, LVEF) plus Gemini augmentation.
- Next step: add nurse/admin gap-fill endpoint for missing hard-stop fields.
