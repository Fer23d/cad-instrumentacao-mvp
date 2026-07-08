from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import secrets
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SYMBOLS_PATH = DATA_DIR / "symbols.json"
HISTORY_PATH = DATA_DIR / "history.json"
PENDING_DIR = DATA_DIR / "pending"


DEFAULT_SYMBOLS = [
    {"id": "camera", "block_pattern": "CAMERA", "layer_pattern": "CFTV", "instrument_type": "Camera / CFTV"},
    {"id": "sensor", "block_pattern": "SENSOR", "layer_pattern": "INSTRUMENTACAO", "instrument_type": "Sensor"},
    {"id": "valvula", "block_pattern": "VALV", "layer_pattern": "AUTOMACAO", "instrument_type": "Valvula"},
    {"id": "pressao", "block_pattern": "PT", "layer_pattern": "INSTRUMENTACAO", "instrument_type": "Transmissor de pressao"},
    {"id": "temperatura", "block_pattern": "TT", "layer_pattern": "INSTRUMENTACAO", "instrument_type": "Transmissor de temperatura"},
]


def ensure_storage() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PENDING_DIR.mkdir(exist_ok=True)
    if not SYMBOLS_PATH.exists():
        _write_json(SYMBOLS_PATH, DEFAULT_SYMBOLS)
    if not HISTORY_PATH.exists():
        _write_json(HISTORY_PATH, [])


def load_symbols() -> list[dict[str, str]]:
    ensure_storage()
    return _read_json(SYMBOLS_PATH, DEFAULT_SYMBOLS)


def save_symbol(block_pattern: str, layer_pattern: str, instrument_type: str) -> dict[str, str]:
    symbols = load_symbols()
    symbol = {
        "id": secrets.token_hex(5),
        "block_pattern": block_pattern.strip(),
        "layer_pattern": layer_pattern.strip(),
        "instrument_type": instrument_type.strip(),
    }
    symbols.append(symbol)
    _write_json(SYMBOLS_PATH, symbols)
    return symbol


def delete_symbol(symbol_id: str) -> None:
    symbols = [symbol for symbol in load_symbols() if symbol.get("id") != symbol_id]
    _write_json(SYMBOLS_PATH, symbols)


def save_pending(analysis_id: str, payload: dict[str, Any]) -> None:
    ensure_storage()
    _write_json(PENDING_DIR / f"{analysis_id}.json", payload)


def load_pending(analysis_id: str) -> dict[str, Any] | None:
    ensure_storage()
    path = PENDING_DIR / f"{analysis_id}.json"
    if not path.exists():
        return None
    return _read_json(path, {})


def add_history(
    analysis: dict[str, Any],
    pdf_name: str,
    excel_name: str,
    marked_dxf_name: str = "",
    marked_dwg_name: str = "",
) -> None:
    history = load_history()
    project = analysis.get("project", {})
    history.insert(
        0,
        {
            "created_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "project_name": project.get("project_name") or "Projeto sem nome",
            "client_name": project.get("client_name") or "-",
            "drawing_type": project.get("drawing_type") or "-",
            "filename": analysis.get("filename") or "-",
            "total_instruments": analysis.get("total_instruments", 0),
            "pending_review": _count_pending_review(analysis.get("instruments", [])),
            "pdf_name": pdf_name,
            "excel_name": excel_name,
            "marked_dxf_name": marked_dxf_name,
            "marked_dwg_name": marked_dwg_name,
        },
    )
    _write_json(HISTORY_PATH, history[:80])


def load_history() -> list[dict[str, Any]]:
    ensure_storage()
    return _read_json(HISTORY_PATH, [])


def dashboard_stats() -> dict[str, int]:
    history = load_history()
    return {
        "projects": len(history),
        "instruments": sum(int(item.get("total_instruments", 0)) for item in history),
        "pending": sum(int(item.get("pending_review", 0)) for item in history),
        "reports": len([item for item in history if item.get("pdf_name")]),
    }


def _count_pending_review(instruments: list[dict[str, Any]]) -> int:
    return len(
        [
            item
            for item in instruments
            if item.get("confidence") == "Baixa"
            or str(item.get("tag", "")).startswith("SEM-TAG")
            or item.get("instrument_type") == "Instrumento nao classificado"
        ]
    )


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
