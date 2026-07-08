from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import math
import re
from typing import Iterable


INSTRUMENT_KEYWORDS = {
    "PT": "Transmissor de pressao",
    "TT": "Transmissor de temperatura",
    "FT": "Transmissor de vazao",
    "LT": "Transmissor de nivel",
    "FV": "Valvula de controle",
    "VALVULA": "Valvula",
    "VALV": "Valvula",
    "CAMERA": "Camera / CFTV",
    "CFTV": "Camera / CFTV",
    "CAM": "Camera / CFTV",
    "PRESENCA": "Sensor de presenca",
    "TEMP": "Sensor de temperatura",
    "SENSOR": "Sensor",
    "SENS": "Sensor",
    "MEDIDOR": "Medidor",
    "FLOW": "Medidor de vazao",
    "INSTR": "Instrumento",
    "I/O": "Ponto de automacao",
}


TAG_PATTERN = re.compile(
    r"\b(?:[A-Z]{1,4}[- ]?\d{1,4}[A-Z]?|CAM[- ]?\d{1,4}|SENSOR[- ]?\d{1,4})\b",
    re.IGNORECASE,
)


@dataclass
class CadEntity:
    entity_type: str
    name: str
    layer: str
    text: str
    x: float
    y: float
    rotation: float = 0.0


@dataclass
class Instrument:
    sequence: int
    tag: str
    instrument_type: str
    source: str
    block_name: str
    layer: str
    x: float
    y: float
    confidence: str
    notes: str


def analyze_cad(path: str | Path, symbol_rules: list[dict] | None = None) -> dict:
    """Analyze an ASCII DXF file and return detected instrumentation items."""
    file_path = Path(path)
    pairs = _read_dxf_pairs(file_path)
    entities = _extract_entities(pairs)
    instruments = _detect_instruments(entities, symbol_rules or [])

    return {
        "filename": file_path.name,
        "total_entities": len(entities),
        "total_instruments": len(instruments),
        "instruments": [asdict(item) for item in instruments],
        "summary_by_type": _summary_by_type(instruments),
        "warnings": _build_warnings(entities, instruments),
    }


def _read_dxf_pairs(path: Path) -> list[tuple[str, str]]:
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    pairs: list[tuple[str, str]] = []
    iterator = iter(content)

    for code in iterator:
        value = next(iterator, "")
        pairs.append((code.strip(), value.strip()))

    return pairs


def _extract_entities(pairs: list[tuple[str, str]]) -> list[CadEntity]:
    supported = {"INSERT", "TEXT", "MTEXT"}
    entities: list[CadEntity] = []
    current_type: str | None = None
    current_data: dict[str, list[str]] = {}

    for code, value in pairs:
        if code == "0":
            if current_type in supported:
                entities.append(_entity_from_data(current_type, current_data))

            current_type = value if value in supported else None
            current_data = {}
            continue

        if current_type in supported:
            current_data.setdefault(code, []).append(value)

    if current_type in supported:
        entities.append(_entity_from_data(current_type, current_data))

    return entities


def _entity_from_data(entity_type: str, data: dict[str, list[str]]) -> CadEntity:
    def first(code: str, default: str = "") -> str:
        values = data.get(code)
        return values[0] if values else default

    def number(code: str) -> float:
        try:
            return float(first(code, "0"))
        except ValueError:
            return 0.0

    text = first("1")
    if entity_type == "MTEXT" and data.get("3"):
        text = "".join(data.get("3", [])) + text

    return CadEntity(
        entity_type=entity_type,
        name=first("2"),
        layer=first("8", "0"),
        text=_clean_text(text),
        x=number("10"),
        y=number("20"),
        rotation=number("50"),
    )


def _detect_instruments(entities: list[CadEntity], symbol_rules: list[dict]) -> list[Instrument]:
    text_entities = [entity for entity in entities if entity.entity_type in {"TEXT", "MTEXT"}]
    insert_candidates = [
        entity
        for entity in entities
        if entity.entity_type == "INSERT" and _looks_like_instrument(entity, symbol_rules)
    ]
    text_candidates = [
        entity
        for entity in text_entities
        if _looks_like_standalone_text_instrument(entity, symbol_rules)
        and not _has_nearby_insert(entity, insert_candidates)
    ]
    candidates = insert_candidates or text_candidates
    instruments: list[Instrument] = []

    for index, entity in enumerate(candidates, start=1):
        nearby_texts = _nearby_texts(entity, text_entities)
        tag = _find_tag(entity, nearby_texts)
        instrument_type = _classify_instrument(entity, nearby_texts, symbol_rules)
        confidence = _confidence(entity, tag, instrument_type)
        notes = []

        if not tag:
            notes.append("Sem tag identificada")
        if entity.entity_type != "INSERT":
            notes.append("Detectado por texto/layer, nao por bloco")

        instruments.append(
            Instrument(
                sequence=index,
                tag=tag or f"SEM-TAG-{index:03d}",
                instrument_type=instrument_type,
                source=entity.entity_type,
                block_name=entity.name or "-",
                layer=entity.layer or "0",
                x=round(entity.x, 3),
                y=round(entity.y, 3),
                confidence=confidence,
                notes="; ".join(notes) if notes else "OK",
            )
        )

    return instruments


def _looks_like_instrument(entity: CadEntity, symbol_rules: list[dict]) -> bool:
    haystack = " ".join([entity.name, entity.layer, entity.text]).upper()
    return (
        _match_symbol_rule(entity, symbol_rules) is not None
        or any(_contains_keyword(haystack, keyword) for keyword in INSTRUMENT_KEYWORDS)
        or bool(TAG_PATTERN.search(haystack))
    )


def _looks_like_standalone_text_instrument(entity: CadEntity, symbol_rules: list[dict]) -> bool:
    text = (entity.text or "").strip()
    if TAG_PATTERN.fullmatch(text):
        return False
    haystack = " ".join([entity.layer, text]).upper()
    return _match_symbol_rule(entity, symbol_rules) is not None or any(
        _contains_keyword(haystack, keyword) for keyword in INSTRUMENT_KEYWORDS
    )


def _has_nearby_insert(entity: CadEntity, inserts: Iterable[CadEntity], radius: float = 80.0) -> bool:
    return any(math.dist((entity.x, entity.y), (insert.x, insert.y)) <= radius for insert in inserts)


def _nearby_texts(entity: CadEntity, text_entities: Iterable[CadEntity], radius: float = 350.0) -> list[CadEntity]:
    nearby: list[tuple[float, CadEntity]] = []

    for text_entity in text_entities:
        distance = math.dist((entity.x, entity.y), (text_entity.x, text_entity.y))
        if distance <= radius:
            nearby.append((distance, text_entity))

    return [item for _, item in sorted(nearby, key=lambda row: row[0])[:5]]


def _find_tag(entity: CadEntity, nearby_texts: list[CadEntity]) -> str:
    fields = [entity.name, entity.text, *[text.text for text in nearby_texts]]
    for field in fields:
        match = TAG_PATTERN.search(field or "")
        if match:
            return match.group(0).replace(" ", "-").upper()
    return ""


def _classify_instrument(entity: CadEntity, nearby_texts: list[CadEntity], symbol_rules: list[dict]) -> str:
    rule = _match_symbol_rule(entity, symbol_rules)
    if rule:
        return rule.get("instrument_type") or "Instrumento"

    direct_text = " ".join([entity.name, entity.layer, entity.text]).upper()
    nearby_text = " ".join(text.text for text in nearby_texts).upper()

    for keyword, instrument_type in INSTRUMENT_KEYWORDS.items():
        if _contains_keyword(direct_text, keyword):
            return instrument_type

    for keyword, instrument_type in INSTRUMENT_KEYWORDS.items():
        if _contains_keyword(nearby_text, keyword):
            return instrument_type

    return "Instrumento nao classificado"


def _match_symbol_rule(entity: CadEntity, symbol_rules: list[dict]) -> dict | None:
    block_name = (entity.name or "").upper()
    layer = (entity.layer or "").upper()

    for rule in symbol_rules:
        block_pattern = (rule.get("block_pattern") or "").strip().upper()
        layer_pattern = (rule.get("layer_pattern") or "").strip().upper()

        block_matches = not block_pattern or block_pattern in block_name
        layer_matches = not layer_pattern or layer_pattern in layer

        if block_matches and layer_matches and (block_pattern or layer_pattern):
            return rule

    return None


def _contains_keyword(text: str, keyword: str) -> bool:
    escaped = re.escape(keyword)
    if len(keyword) <= 3 or "/" in keyword:
        return bool(re.search(rf"(?<![A-Z0-9]){escaped}(?![A-Z0-9])", text))
    return keyword in text


def _confidence(entity: CadEntity, tag: str, instrument_type: str) -> str:
    if entity.entity_type == "INSERT" and tag and instrument_type != "Instrumento nao classificado":
        return "Alta"
    if tag or entity.entity_type == "INSERT":
        return "Media"
    return "Baixa"


def _summary_by_type(instruments: list[Instrument]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in instruments:
        summary[item.instrument_type] = summary.get(item.instrument_type, 0) + 1
    return dict(sorted(summary.items()))


def _build_warnings(entities: list[CadEntity], instruments: list[Instrument]) -> list[str]:
    warnings = []
    if not entities:
        warnings.append("Nenhuma entidade INSERT/TEXT/MTEXT foi encontrada. Confirme se o arquivo DXF esta em ASCII.")
    if not instruments:
        warnings.append("Nenhum instrumento foi identificado. Talvez os blocos/layers usem nomes diferentes dos cadastrados.")
    if any(item.tag.startswith("SEM-TAG") for item in instruments):
        warnings.append("Alguns instrumentos foram encontrados sem tag proxima.")
    return warnings


def _clean_text(value: str) -> str:
    return (
        value.replace("\\P", " ")
        .replace("{", "")
        .replace("}", "")
        .replace("\\~", " ")
        .strip()
    )
