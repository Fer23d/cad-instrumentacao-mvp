from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess


class DwgConversionError(RuntimeError):
    pass


def convert_dwg_to_dxf(dwg_path: str | Path, output_dir: str | Path) -> Path:
    """Convert a DWG file to DXF using ODA File Converter when available."""
    return _convert_with_oda(dwg_path, output_dir, "DXF", "*.dxf")


def convert_dxf_to_dwg(dxf_path: str | Path, output_dir: str | Path) -> Path:
    """Convert a DXF file to DWG using ODA File Converter when available."""
    return _convert_with_oda(dxf_path, output_dir, "DWG", "*.dwg")


def _convert_with_oda(source_path: str | Path, output_dir: str | Path, output_type: str, expected_glob: str) -> Path:
    source = Path(source_path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    converter = _find_oda_converter()
    if converter is None:
        raise DwgConversionError(
            "Arquivo CAD recebido, mas nenhum conversor ODA foi encontrado. "
            "Instale o ODA File Converter ou configure a variavel ODA_FILE_CONVERTER."
        )

    input_dir = source.parent
    before = set(target_dir.glob(expected_glob))

    command = [
        str(converter),
        str(input_dir),
        str(target_dir),
        "ACAD2018",
        output_type,
        "0",
        "1",
        source.name,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise DwgConversionError(
            f"Falha ao converter arquivo CAD para {output_type}. "
            f"Saida: {(result.stderr or result.stdout or '').strip()}"
        )

    expected = target_dir / f"{source.stem}.{output_type.lower()}"
    if expected.exists():
        return expected

    after = set(target_dir.glob(expected_glob))
    created = sorted(after - before, key=lambda item: item.stat().st_mtime, reverse=True)
    if created:
        return created[0]

    raise DwgConversionError(f"O conversor terminou, mas nenhum arquivo {output_type} foi gerado.")


def _find_oda_converter() -> Path | None:
    configured = os.environ.get("ODA_FILE_CONVERTER")
    if configured and Path(configured).exists():
        return Path(configured)

    executable = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if executable:
        return Path(executable)

    candidates = [
        Path(r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe"),
        Path(r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe"),
        Path(r"C:\Program Files\ODA\ODA File Converter\ODAFileConverter.exe"),
        Path(r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe"),
        Path("/usr/bin/ODAFileConverter"),
        Path("/usr/local/bin/ODAFileConverter"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    for root in [Path(r"C:\Program Files\ODA"), Path(r"C:\Program Files (x86)\ODA")]:
        if root.exists():
            matches = sorted(root.glob("**/ODAFileConverter.exe"))
            if matches:
                return matches[-1]

    return None
