from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.gemini_client import GeminiError
from app.pipeline import run_extraction

app = FastAPI(title="Oyster Discharge Extractor", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/care-plan", response_class=HTMLResponse)
def care_plan_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("care_plan.html", {"request": request})


@app.get("/careplan")
def care_plan_alias() -> RedirectResponse:
    return RedirectResponse(url="/care-plan", status_code=307)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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

    return output
