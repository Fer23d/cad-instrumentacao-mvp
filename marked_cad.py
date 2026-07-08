from __future__ import annotations

from pathlib import Path
from typing import Any


MARKUP_LAYER = "INSTRUMENTACAD_MARCACOES"


def create_marked_dxf(
    source_dxf: str | Path,
    instruments: list[dict[str, Any]],
    output_path: str | Path,
    project: dict[str, str] | None = None,
) -> Path:
    source = Path(source_dxf)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    marker_lines = _build_marker_entities(instruments, project or {})
    output_lines = _insert_entities(lines, marker_lines)
    target.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return target


def _insert_entities(lines: list[str], marker_lines: list[str]) -> list[str]:
    pairs = _to_pairs(lines)
    entities_start = _find_entities_start(pairs)

    if entities_start is None:
        return _append_entities_section(lines, marker_lines)

    end_pair_index = _find_section_end(pairs, entities_start + 1)
    if end_pair_index is None:
        return _append_entities_section(lines, marker_lines)

    insert_line_index = end_pair_index * 2
    return lines[:insert_line_index] + marker_lines + lines[insert_line_index:]


def _append_entities_section(lines: list[str], marker_lines: list[str]) -> list[str]:
    stripped = list(lines)
    if len(stripped) >= 2 and stripped[-2].strip() == "0" and stripped[-1].strip().upper() == "EOF":
        stripped = stripped[:-2]
    return stripped + ["0", "SECTION", "2", "ENTITIES"] + marker_lines + ["0", "ENDSEC", "0", "EOF"]


def _to_pairs(lines: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(lines), 2):
        code = lines[index].strip()
        value = lines[index + 1].strip() if index + 1 < len(lines) else ""
        pairs.append((code, value))
    return pairs


def _find_entities_start(pairs: list[tuple[str, str]]) -> int | None:
    for index in range(len(pairs) - 1):
        if pairs[index] == ("0", "SECTION") and pairs[index + 1][0] == "2" and pairs[index + 1][1].upper() == "ENTITIES":
            return index
    return None


def _find_section_end(pairs: list[tuple[str, str]], start_index: int) -> int | None:
    for index in range(start_index, len(pairs)):
        if pairs[index][0] == "0" and pairs[index][1].upper() == "ENDSEC":
            return index
    return None


def _build_marker_entities(instruments: list[dict[str, Any]], project: dict[str, str]) -> list[str]:
    if not instruments:
        return []

    xs = [_number(item.get("x")) for item in instruments]
    ys = [_number(item.get("y")) for item in instruments]
    extent = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    text_height = min(max(extent * 0.018, 2.5), 250.0)
    radius = text_height * 2.2
    entities: list[str] = []

    for item in instruments:
        x = _number(item.get("x"))
        y = _number(item.get("y"))
        color = _status_color(item)
        text_x = x + radius * 1.6
        text_y = y + radius * 1.6
        label = _safe_text(f"{item.get('tag', '')} | {item.get('instrument_type', '')} | {item.get('confidence', '')}")

        entities.extend(_circle(x, y, radius, color))
        entities.extend(_line(x, y, text_x, text_y, color))
        entities.extend(_text(text_x, text_y, text_height, label, color))

    legend_x = min(xs)
    legend_y = max(ys) + radius * 5
    project_name = project.get("project_name") or "Projeto"
    entities.extend(_text(legend_x, legend_y, text_height * 1.2, _safe_text(f"InstrumentaCAD - Marcacoes: {project_name}"), 5))
    entities.extend(_text(legend_x, legend_y - text_height * 1.8, text_height, "Verde: alta | Amarelo: media/revisar | Vermelho: sem tag | Laranja: sem tipo", 5))
    return entities


def _circle(x: float, y: float, radius: float, color: int) -> list[str]:
    return [
        "0",
        "CIRCLE",
        "8",
        MARKUP_LAYER,
        "62",
        str(color),
        "10",
        _fmt(x),
        "20",
        _fmt(y),
        "30",
        "0",
        "40",
        _fmt(radius),
    ]


def _line(x1: float, y1: float, x2: float, y2: float, color: int) -> list[str]:
    return [
        "0",
        "LINE",
        "8",
        MARKUP_LAYER,
        "62",
        str(color),
        "10",
        _fmt(x1),
        "20",
        _fmt(y1),
        "30",
        "0",
        "11",
        _fmt(x2),
        "21",
        _fmt(y2),
        "31",
        "0",
    ]


def _text(x: float, y: float, height: float, value: str, color: int) -> list[str]:
    return [
        "0",
        "TEXT",
        "8",
        MARKUP_LAYER,
        "62",
        str(color),
        "10",
        _fmt(x),
        "20",
        _fmt(y),
        "30",
        "0",
        "40",
        _fmt(height),
        "1",
        value,
        "50",
        "0",
    ]


def _status_color(item: dict[str, Any]) -> int:
    tag = str(item.get("tag", ""))
    instrument_type = str(item.get("instrument_type", ""))
    confidence = str(item.get("confidence", ""))
    if tag.startswith("SEM-TAG"):
        return 1
    if instrument_type == "Instrumento nao classificado":
        return 30
    if confidence == "Alta":
        return 3
    return 2


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _safe_text(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ").replace("{", "(").replace("}", ")")
