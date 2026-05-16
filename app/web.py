from __future__ import annotations

import os
import threading
import uuid
import csv
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from app.config import load_settings
from app.io.readers import list_excel_sheet_items
from app.main import run_pipeline
from app.web_support import list_downloadable_outputs, load_table_preview, parse_job_options, prepare_input_with_website_url

BASE_DIR = Path(__file__).resolve().parent.parent
RUNS_DIR = BASE_DIR / "web_runs"
UPLOADS_DIR = BASE_DIR / "web_uploads"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
RUNS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class JobState:
    job_id: str
    filename: str
    website_url: str | None
    status: str = "queued"
    stage: str = "queued"
    message: str = "Wartet auf Start"
    current: int = 0
    total: int = 0
    supplier_sku: str | None = None
    summary: dict[str, object] = field(default_factory=dict)
    options: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    output_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        base = {
            "job_id": self.job_id,
            "filename": self.filename,
            "website_url": self.website_url,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "current": self.current,
            "total": self.total,
            "supplier_sku": self.supplier_sku,
            "summary": self.summary,
            "options": self.options,
            "error": self.error,
            "downloads": [],
            "errors_table": {"columns": [], "rows": [], "total_rows": 0},
        }
        if self.output_dir:
            base["downloads"] = list_downloadable_outputs(self.output_dir)
            base["errors_table"] = load_table_preview(Path(self.output_dir) / "errors.csv")
        return base


app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
jobs: dict[str, JobState] = {}
jobs_lock = threading.Lock()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/jobs")
def create_job():
    uploaded_file = request.files.get("file")
    website_url = (request.form.get("website_url") or "").strip() or None
    if (uploaded_file is None or not uploaded_file.filename) and not website_url:
        return jsonify({"error": "Bitte eine Excel-/CSV-Datei hochladen oder eine Webseiten-URL angeben."}), 400

    suffix = ".csv"
    filename = "url_import.csv"
    temp_path: Path
    if uploaded_file is None or not uploaded_file.filename:
        temp_path = _build_url_only_input(
            website_url=website_url,
            supplier_name=(request.form.get("supplier_name") or "").strip() or None,
            purchase_currency=(request.form.get("purchase_currency") or "").strip().upper() or None,
            import_kind=(request.form.get("import_kind") or "").strip() or None,
        )
    else:
        suffix = Path(uploaded_file.filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            return jsonify({"error": "Unterstuetzt werden nur .csv, .xlsx und .xls."}), 400
        filename = secure_filename(uploaded_file.filename) or f"upload{suffix}"
        temp_file = NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOADS_DIR)
        uploaded_file.save(temp_file.name)
        temp_file.close()
        temp_path = Path(temp_file.name)

    base_settings = load_settings(None)
    job_options = parse_job_options(request.form, base_settings)
    import_kind = str(job_options["import_kind"])
    if import_kind not in {"supplier_price_list", "sales_article_list", "magento_1_export"}:
        return jsonify({"error": "Bitte einen Importtyp auswaehlen."}), 400
    if import_kind == "supplier_price_list":
        if not job_options["supplier_name"]:
            return jsonify({"error": "Bei Lieferanten-Preislisten ist Supplier Name erforderlich."}), 400
        if not job_options["purchase_currency"]:
            return jsonify({"error": "Bei Lieferanten-Preislisten ist Currency erforderlich."}), 400
    if import_kind == "magento_1_export" and (uploaded_file is None or not uploaded_file.filename):
        return jsonify({"error": "Beim Magento-1.9-Export ist eine CSV- oder Excel-Datei erforderlich."}), 400
    if import_kind == "magento_1_export" and not website_url:
        return jsonify({"error": "Beim Magento-1.9-Export ist die Webseiten-URL als Basis fuer Produkt- und Asset-Links erforderlich."}), 400
    job_id = uuid.uuid4().hex[:12]
    output_dir = RUNS_DIR / job_id

    job = JobState(
        job_id=job_id,
        filename=filename,
        website_url=website_url,
        output_dir=str(output_dir),
        options={
            "import_kind": import_kind,
            "supplier_name": job_options["supplier_name"],
            "purchase_currency": job_options["purchase_currency"],
            "sheet_name": job_options["sheet_name"],
            "sheet_index": job_options["sheet_index"],
            "source_url_mode": job_options["source_url_mode"],
            "force_crawl": job_options["force_crawl"],
            "export_types": sorted(job_options["export_types"]),
            "settings": job_options["settings"].model_dump(),
        },
    )
    with jobs_lock:
        jobs[job_id] = job

    thread = threading.Thread(target=_run_job, args=(job_id, temp_path, job_options), daemon=True)
    thread.start()
    return jsonify(job.to_dict()), 202


def _build_url_only_input(
    website_url: str | None,
    supplier_name: str | None,
    purchase_currency: str | None,
    import_kind: str | None,
) -> Path:
    temp_file = NamedTemporaryFile(delete=False, suffix=".csv", dir=UPLOADS_DIR)
    temp_path = Path(temp_file.name)
    temp_file.close()
    parsed = urlparse(website_url or "")
    default_sku = parsed.netloc or "url-test"
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "supplier_sku",
                "supplier_name",
                "source_url",
                "title_raw",
                "import_kind",
                "purchase_currency",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "supplier_sku": default_sku,
                "supplier_name": supplier_name or "",
                "source_url": website_url or "",
                "title_raw": parsed.netloc or website_url or "URL Test",
                "import_kind": import_kind or "",
                "purchase_currency": purchase_currency or "",
            }
        )
    return temp_path


@app.post("/api/input-sheets")
def get_input_sheets():
    uploaded_file = request.files.get("file")
    if uploaded_file is None or not uploaded_file.filename:
        return jsonify({"sheets": []})
    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        return jsonify({"sheets": []})
    temp_file = NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOADS_DIR)
    uploaded_file.save(temp_file.name)
    temp_file.close()
    temp_path = Path(temp_file.name)
    try:
        return jsonify({"sheets": list_excel_sheet_items(temp_path)})
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = _get_job(job_id)
    return jsonify(job.to_dict())


@app.get("/download/<job_id>/<path:relative_path>")
def download(job_id: str, relative_path: str):
    job = _get_job(job_id)
    if not job.output_dir:
        abort(404)
    base = Path(job.output_dir)
    target = (base / relative_path).resolve()
    if not target.exists() or os.path.commonpath([str(base.resolve()), str(target)]) != str(base.resolve()):
        abort(404)
    return send_from_directory(base, relative_path, as_attachment=True)


def _run_job(job_id: str, input_path: Path, job_options: dict[str, object]) -> None:
    prepared_input = input_path
    temp_input_to_remove: Path | None = None
    selected_sheet_name = job_options["sheet_name"]
    selected_sheet_index = job_options["sheet_index"]
    try:
        _update_job(job_id, status="running", stage="preparing", message="Bereite Import vor")
        website_url = _get_job(job_id).website_url
        prepared_input = prepare_input_with_website_url(
            input_path,
            website_url,
            source_url_mode=str(job_options["source_url_mode"]),
            force_crawl=bool(job_options["force_crawl"]),
            import_kind=str(job_options["import_kind"]) or None,
            supplier_name=str(job_options["supplier_name"]) if job_options["supplier_name"] else None,
            purchase_currency=str(job_options["purchase_currency"]) if job_options["purchase_currency"] else None,
            sheet_name=str(job_options["sheet_name"]) if job_options["sheet_name"] else None,
            sheet_index=job_options["sheet_index"],
        )
        if prepared_input != input_path:
            temp_input_to_remove = prepared_input
            if prepared_input.suffix.lower() in {".xlsx", ".xls"}:
                selected_sheet_index = 0

        settings = job_options["settings"]
        summary = run_pipeline(
            prepared_input,
            _get_job(job_id).output_dir,
            settings,
            progress_callback=lambda payload: _update_job(job_id, **payload),
            export_types=set(job_options["export_types"]),
            sheet_name=selected_sheet_name,
            sheet_index=selected_sheet_index,
        )
        _update_job(job_id, status="completed", stage="completed", message="Import abgeschlossen", summary=summary)
    except Exception as exc:
        _update_job(job_id, status="failed", stage="failed", message="Import fehlgeschlagen", error=str(exc))
    finally:
        input_path.unlink(missing_ok=True)
        if temp_input_to_remove is not None:
            temp_input_to_remove.unlink(missing_ok=True)


def _get_job(job_id: str) -> JobState:
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        abort(404)
    return job


def _update_job(job_id: str, **changes: object) -> None:
    with jobs_lock:
        job = jobs[job_id]
        for key, value in changes.items():
            if hasattr(job, key):
                setattr(job, key, value)


def main() -> int:
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8000"))
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
