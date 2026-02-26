from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.gemini_client import GeminiError
from app.config import settings
from app.pipeline import run_extraction
from app.storage import get_extraction, init_db, list_extractions, save_extraction
from app.summary import build_simplified_summary

app = FastAPI(title="Oyster Discharge Extractor", version="0.1.0")

import hashlib as _hashlib

_static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

# Cache-bust token: hash of css+js content at startup
_bust = _hashlib.md5(
    b"".join(p.read_bytes() for p in sorted(_static_dir.glob("*")) if p.is_file()),
    usedforsecurity=False,
).hexdigest()[:8]
templates.env.globals["v"] = _bust


@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=60"
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/care-plan", response_class=HTMLResponse)
def care_plan_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("care_plan.html", {"request": request})


@app.get("/calendar-view", response_class=HTMLResponse)
def calendar_view_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("calendar_view.html", {"request": request})


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("history.html", {"request": request})


@app.get("/summary/{extraction_id}", response_class=HTMLResponse)
def summary_page(request: Request, extraction_id: int) -> HTMLResponse:
    record = get_extraction(extraction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return templates.TemplateResponse(
        "summary.html",
        {
            "request": request,
            "record": record,
        },
    )


@app.get("/careplan")
def care_plan_alias() -> RedirectResponse:
    return RedirectResponse(url="/care-plan", status_code=307)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "config": {
            "gemini_api_key_configured": bool(settings.gemini_api_key),
            "landing_api_key_configured": bool(settings.landing_api_key),
        },
    }


@app.get("/api/extractions")
def extractions_api(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {"items": list_extractions(limit=limit)}


@app.get("/api/extractions/{extraction_id}")
def extraction_by_id_api(extraction_id: int) -> dict:
    record = get_extraction(extraction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Extraction not found")
    return record


@app.post("/extract")
async def extract(pdf: UploadFile = File(...)) -> dict:
    if pdf.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail="Upload a PDF file.")

    suffix = Path(pdf.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        raw = await pdf.read()
        tmp.write(raw)
        tmp.flush()

        try:
            output = run_extraction(tmp.name)
        except (GeminiError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc

    summary_text = build_simplified_summary(output)
    extraction_id = save_extraction(output, summary_text)
    output["extraction_id"] = extraction_id
    output["simplified_summary"] = summary_text
    return output
