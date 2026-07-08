from __future__ import annotations

from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
from urllib.parse import parse_qs, urlparse

from cad_analyzer import analyze_cad
from dwg_converter import DwgConversionError, convert_dwg_to_dxf, convert_dxf_to_dwg
from marked_cad import create_marked_dxf
from reports import create_excel_report, create_pdf_report
from storage import (
    add_history,
    dashboard_stats,
    delete_symbol,
    ensure_storage,
    load_history,
    load_pending,
    load_symbols,
    save_pending,
    save_symbol,
)


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
REPORT_DIR = BASE_DIR / "reports"
MAX_UPLOAD_SIZE = 25 * 1024 * 1024


class CadInstrumentationHandler(BaseHTTPRequestHandler):
    server_version = "CADInstrumentationMVP/0.2"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_dashboard_page())
            return
        if parsed.path == "/symbols":
            self._send_html(_symbols_page())
            return
        if parsed.path.startswith("/download/"):
            self._download_file(parsed.path.removeprefix("/download/"))
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/health":
            self._send_json({"ok": True})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Pagina nao encontrada")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        routes = {
            "/analyze": self._handle_analyze,
            "/finalize": self._handle_finalize,
            "/symbols": self._handle_symbols,
        }
        handler = routes.get(parsed.path)
        if handler is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Rota nao encontrada")
            return
        handler()

    def _handle_analyze(self) -> None:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length > MAX_UPLOAD_SIZE:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Arquivo maior que 25 MB")
            return

        body = self.rfile.read(content_length)
        fields, files = _parse_form(body, self.headers.get("content-type", ""))
        upload = files.get("cad_file")
        if upload is None:
            self._send_error(HTTPStatus.BAD_REQUEST, "Envie um arquivo DXF ou DWG")
            return

        original_name, file_data = upload
        extension = Path(original_name).suffix.lower()
        if extension not in {".dxf", ".dwg"}:
            self._send_error(HTTPStatus.BAD_REQUEST, "Envie um arquivo .dxf ou .dwg")
            return

        upload_id = secrets.token_hex(8)
        upload_path = UPLOAD_DIR / f"{upload_id}-{_safe_filename(original_name)}"
        upload_path.parent.mkdir(exist_ok=True)
        upload_path.write_bytes(file_data)
        logo_path = _save_logo(upload_id, files.get("company_logo"))

        try:
            analysis_path = upload_path
            converted_from_dwg = False
            if extension == ".dwg":
                analysis_path = convert_dwg_to_dxf(upload_path, UPLOAD_DIR / f"{upload_id}-converted")
                converted_from_dwg = True

            analysis = analyze_cad(analysis_path, load_symbols())
            if converted_from_dwg:
                analysis["filename"] = f"{original_name} convertido para {Path(analysis_path).name}"
            analysis["project"] = _project_from_fields(fields)
            if logo_path is not None:
                analysis["project"]["logo_path"] = str(logo_path)
            analysis["source_dxf_path"] = str(analysis_path)
            analysis["original_file_path"] = str(upload_path)
            analysis["original_extension"] = extension
        except DwgConversionError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Erro ao analisar arquivo: {exc}")
            return

        save_pending(upload_id, analysis)
        self._send_html(_review_page(upload_id, analysis))

    def _handle_finalize(self) -> None:
        content_length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(content_length)
        fields, _ = _parse_form(body, self.headers.get("content-type", ""))
        analysis_id = fields.get("analysis_id", "")
        analysis = load_pending(analysis_id)
        if analysis is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Analise nao encontrada")
            return

        instruments = _reviewed_instruments(fields)
        analysis["instruments"] = instruments
        analysis["total_instruments"] = len(instruments)
        analysis["summary_by_type"] = _summary_by_type(instruments)
        analysis["warnings"] = _warnings_for_reviewed(instruments)

        report_base = f"{analysis_id}-relatorio-instrumentacao"
        pdf_path = create_pdf_report(analysis, REPORT_DIR / f"{report_base}.pdf")
        excel_path = create_excel_report(analysis, REPORT_DIR / f"{report_base}.xlsx")
        marked_dxf_name = ""
        marked_dwg_name = ""
        try:
            marked_dxf_path = create_marked_dxf(
                analysis.get("source_dxf_path", ""),
                instruments,
                REPORT_DIR / f"{report_base}-planta-marcada.dxf",
                analysis.get("project", {}),
            )
            marked_dxf_name = marked_dxf_path.name
            try:
                marked_dwg_path = convert_dxf_to_dwg(marked_dxf_path, REPORT_DIR)
                marked_dwg_name = marked_dwg_path.name
            except DwgConversionError as exc:
                analysis.setdefault("warnings", []).append(f"DXF marcado gerado, mas nao foi possivel gerar DWG marcado: {exc}")
        except Exception as exc:
            analysis.setdefault("warnings", []).append(f"Nao foi possivel gerar planta marcada: {exc}")

        analysis["marked_dxf_name"] = marked_dxf_name
        analysis["marked_dwg_name"] = marked_dwg_name
        add_history(analysis, pdf_path.name, excel_path.name, marked_dxf_name, marked_dwg_name)
        save_pending(analysis_id, analysis)

        self._send_html(_final_page(analysis, pdf_path.name, excel_path.name, marked_dxf_name, marked_dwg_name))

    def _handle_symbols(self) -> None:
        content_length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(content_length)
        fields, _ = _parse_form(body, self.headers.get("content-type", ""))

        if fields.get("action") == "delete":
            delete_symbol(fields.get("symbol_id", ""))
        else:
            instrument_type = fields.get("instrument_type", "").strip()
            block_pattern = fields.get("block_pattern", "").strip()
            layer_pattern = fields.get("layer_pattern", "").strip()
            if instrument_type and (block_pattern or layer_pattern):
                save_symbol(block_pattern, layer_pattern, instrument_type)

        self._redirect("/symbols")

    def _download_file(self, requested_name: str) -> None:
        filename = Path(requested_name).name
        file_path = REPORT_DIR / filename
        if not file_path.exists():
            self._send_error(HTTPStatus.NOT_FOUND, "Arquivo nao encontrado")
            return

        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.end_headers()
        with file_path.open("rb") as file:
            shutil.copyfileobj(file, self.wfile)

    def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, path: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.end_headers()

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_html(_error_page(message), status)


def _dashboard_page() -> str:
    stats = dashboard_stats()
    symbol_count = len(load_symbols())
    history_rows = "\n".join(_history_row(item) for item in load_history()[:10])
    if not history_rows:
        history_rows = '<tr><td colspan="7" class="empty">Nenhuma analise finalizada ainda.</td></tr>'

    return _layout(
        f"""
        <section class="hero-app">
          <div class="hero-copy">
            <p class="eyebrow">Software industrial para engenharia</p>
            <h1>InstrumentaCAD</h1>
            <p class="lead">Analise plantas CAD de instrumentacao, localize tags e simbolos, revise os pontos encontrados e gere relatorios tecnicos com rastreabilidade.</p>
            <div class="hero-actions">
              <a class="button" href="#cad-upload">Enviar Planta CAD</a>
              <a class="button secondary" href="/symbols">Biblioteca de simbolos</a>
            </div>
            <div class="process-strip">
              <span>01 Upload DXF/DWG</span>
              <span>02 Revisao tecnica</span>
              <span>03 PDF, Excel e planta marcada</span>
            </div>
          </div>
          <form id="cad-upload" class="upload-panel" action="/analyze" method="post" enctype="multipart/form-data">
            <div>
              <p class="eyebrow">Nova analise</p>
              <h2>Enviar Planta CAD</h2>
            </div>
            <div class="form-grid">
              <label>Nome do projeto<input name="project_name" placeholder="Ex.: Hospital - Pavimento 02" required></label>
              <label>Cliente<input name="client_name" placeholder="Ex.: ACME Engenharia"></label>
              <label>Responsavel tecnico<input name="technical_owner" placeholder="Ex.: Eng. Responsavel"></label>
              <label>Tipo da planta
                <select name="drawing_type">
                  <option>Instrumentacao</option>
                  <option>Automacao</option>
                  <option>CFTV</option>
                  <option>Eletrica</option>
                  <option>Incendio</option>
                  <option>Arquitetura</option>
                </select>
              </label>
            </div>
            <label>Arquivo CAD<input type="file" name="cad_file" accept=".dxf,.dwg" required></label>
            <label>Logo da empresa para o relatorio<input type="file" name="company_logo" accept=".png,.jpg,.jpeg"></label>
            <button type="submit">Enviar Planta CAD</button>
            <p class="hint">DXF funciona direto. DWG usa ODA File Converter instalado no servidor.</p>
          </form>
        </section>

        <section class="stats">
          <div><span>Plantas analisadas</span><strong>{stats["projects"]}</strong><small>Projetos finalizados</small></div>
          <div><span>Instrumentos encontrados</span><strong>{stats["instruments"]}</strong><small>Tags e simbolos detectados</small></div>
          <div><span>Pendentes de revisao</span><strong>{stats["pending"]}</strong><small>Itens com baixa confianca</small></div>
          <div><span>Regras ativas</span><strong>{symbol_count}</strong><small>Biblioteca de simbolos</small></div>
        </section>

        <section class="workspace-grid">
          <div>
            <section class="section-head">
              <div><h2>Historico recente</h2><p>Ultimos relatorios gerados pelo sistema.</p></div>
            </section>
            <section class="table-wrap">
              <table>
                <thead><tr><th>Data</th><th>Projeto</th><th>Cliente</th><th>Tipo</th><th>Instrumentos</th><th>Pendencias</th><th>Downloads</th></tr></thead>
                <tbody>{history_rows}</tbody>
              </table>
            </section>
          </div>
          <aside class="symbols-card">
            <p class="eyebrow">Biblioteca de simbolos</p>
            <h2>Regras por bloco e layer</h2>
            <p>Configure padroes usados nos desenhos para aumentar a precisao da classificacao automatica.</p>
            <dl>
              <div><dt>Blocos</dt><dd>CAMERA, SENSOR, PT, TT, VALV</dd></div>
              <div><dt>Layers</dt><dd>CFTV, AUTOMACAO, INSTRUMENTACAO</dd></div>
              <div><dt>Saida</dt><dd>PDF, Excel, DXF marcado e DWG opcional</dd></div>
            </dl>
            <a class="button secondary" href="/symbols">Abrir biblioteca</a>
          </aside>
        </section>
        """,
        active="dashboard",
    )


def _symbols_page() -> str:
    rows = "\n".join(_symbol_row(symbol) for symbol in load_symbols())
    if not rows:
        rows = '<tr><td colspan="5" class="empty">Nenhum simbolo cadastrado.</td></tr>'

    return _layout(
        f"""
        <section class="page-title">
          <p class="eyebrow">Biblioteca</p>
          <h1>Simbolos e regras de identificacao</h1>
          <p class="lead">Cadastre padroes de bloco e layer para adaptar o sistema ao CAD de cada cliente.</p>
        </section>

        <form class="panel" action="/symbols" method="post">
          <div class="form-grid three">
            <label>Padrao do bloco<input name="block_pattern" placeholder="Ex.: CAMERA_DOME"></label>
            <label>Padrao do layer<input name="layer_pattern" placeholder="Ex.: CFTV"></label>
            <label>Tipo do instrumento<input name="instrument_type" placeholder="Ex.: Camera / CFTV" required></label>
          </div>
          <button type="submit">Adicionar simbolo</button>
        </form>

        <section class="table-wrap">
          <table>
            <thead><tr><th>Bloco contem</th><th>Layer contem</th><th>Tipo</th><th>ID</th><th>Acao</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        """,
        active="symbols",
    )


def _review_page(analysis_id: str, analysis: dict) -> str:
    rows = "\n".join(_review_row(index, item) for index, item in enumerate(analysis["instruments"]))
    if not rows:
        rows = '<tr><td colspan="9" class="empty">Nenhum instrumento encontrado. Revise a biblioteca de simbolos.</td></tr>'

    summary_items = "\n".join(
        f"<li><strong>{html.escape(name)}</strong><span>{count}</span></li>"
        for name, count in analysis["summary_by_type"].items()
    ) or "<li><strong>Nenhum tipo</strong><span>0</span></li>"

    return _layout(
        f"""
        <section class="result-header">
          <div>
            <p class="eyebrow">Revisao tecnica</p>
            <h1>{html.escape(analysis["project"].get("project_name") or analysis["filename"])}</h1>
            <p class="lead">{analysis["total_instruments"]} instrumento(s) encontrados. Corrija tags, tipos e observacoes antes de gerar o relatorio final.</p>
          </div>
          <ul class="summary">{summary_items}</ul>
        </section>
        <form action="/finalize" method="post">
          <input type="hidden" name="analysis_id" value="{html.escape(analysis_id)}">
          <input type="hidden" name="count" value="{len(analysis["instruments"])}">
          <section class="table-wrap">
            <table>
              <thead>
                <tr><th>Usar</th><th>Seq.</th><th>Tag</th><th>Tipo</th><th>Bloco</th><th>Layer</th><th>X/Y</th><th>Conf.</th><th>Obs.</th></tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
          </section>
          <div class="actions footer-actions">
            <a class="button secondary" href="/">Cancelar</a>
            <button type="submit">Gerar PDF e Excel</button>
          </div>
        </form>
        """,
        active="dashboard",
    )


def _final_page(analysis: dict, pdf_name: str, excel_name: str, marked_dxf_name: str, marked_dwg_name: str) -> str:
    rows = "\n".join(_instrument_row_html(item) for item in analysis["instruments"])
    if not rows:
        rows = '<tr><td colspan="10" class="empty">Nenhum instrumento aprovado.</td></tr>'
    dxf_button = (
        f'<a class="button secondary" href="/download/{html.escape(marked_dxf_name)}">Baixar DXF marcado</a>'
        if marked_dxf_name
        else ""
    )
    dwg_button = (
        f'<a class="button" href="/download/{html.escape(marked_dwg_name)}">Baixar DWG marcado</a>'
        if marked_dwg_name
        else ""
    )

    return _layout(
        f"""
        <section class="success">
          <p class="eyebrow">Relatorio gerado</p>
          <h1>{html.escape(analysis["project"].get("project_name") or analysis["filename"])}</h1>
          <p class="lead">Arquivos finais prontos para compartilhar com cliente, engenharia ou obra.</p>
          <div class="actions">
            <a class="button secondary" href="/download/{html.escape(excel_name)}">Baixar Excel</a>
            {dxf_button}
            {dwg_button}
            <a class="button" href="/download/{html.escape(pdf_name)}">Baixar PDF</a>
          </div>
        </section>
        <section class="table-wrap">
          <table>
            <thead><tr><th>Seq.</th><th>Tag</th><th>Tipo</th><th>Origem</th><th>Bloco</th><th>Layer</th><th>X</th><th>Y</th><th>Conf.</th><th>Obs.</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        """,
        active="dashboard",
    )


def _history_row(item: dict) -> str:
    download_links = []
    for key, label in [
        ("excel_name", "Excel"),
        ("pdf_name", "PDF"),
        ("marked_dxf_name", "DXF"),
        ("marked_dwg_name", "DWG"),
    ]:
        if item.get(key):
            download_links.append(f'<a href="/download/{html.escape(item.get(key, ""))}">{label}</a>')
    downloads = " ".join(download_links) or "-"
    cells = [
        item.get("created_at", "-"),
        item.get("project_name", "-"),
        item.get("client_name", "-"),
        item.get("drawing_type", "-"),
        item.get("total_instruments", 0),
        item.get("pending_review", 0),
        downloads,
    ]
    return "<tr>" + "".join(f"<td>{cell if index == 6 else html.escape(str(cell))}</td>" for index, cell in enumerate(cells)) + "</tr>"


def _symbol_row(symbol: dict) -> str:
    return f"""
    <tr>
      <td>{html.escape(symbol.get("block_pattern") or "-")}</td>
      <td>{html.escape(symbol.get("layer_pattern") or "-")}</td>
      <td>{html.escape(symbol.get("instrument_type") or "-")}</td>
      <td>{html.escape(symbol.get("id") or "-")}</td>
      <td>
        <form action="/symbols" method="post" class="inline-form">
          <input type="hidden" name="action" value="delete">
          <input type="hidden" name="symbol_id" value="{html.escape(symbol.get("id") or "")}">
          <button class="link-button" type="submit">Remover</button>
        </form>
      </td>
    </tr>
    """


def _review_row(index: int, item: dict) -> str:
    return f"""
    <tr>
      <td><input type="checkbox" name="use_{index}" value="1" checked></td>
      <td>{item["sequence"]}<input type="hidden" name="sequence_{index}" value="{item["sequence"]}"></td>
      <td><input name="tag_{index}" value="{html.escape(str(item["tag"]))}"></td>
      <td><input name="type_{index}" value="{html.escape(str(item["instrument_type"]))}"></td>
      <td>{html.escape(str(item["block_name"]))}<input type="hidden" name="block_{index}" value="{html.escape(str(item["block_name"]))}"></td>
      <td>{html.escape(str(item["layer"]))}<input type="hidden" name="layer_{index}" value="{html.escape(str(item["layer"]))}"></td>
      <td>{item["x"]} / {item["y"]}<input type="hidden" name="x_{index}" value="{item["x"]}"><input type="hidden" name="y_{index}" value="{item["y"]}"></td>
      <td>{html.escape(str(item["confidence"]))}<input type="hidden" name="confidence_{index}" value="{html.escape(str(item["confidence"]))}"><input type="hidden" name="source_{index}" value="{html.escape(str(item["source"]))}"></td>
      <td><input name="notes_{index}" value="{html.escape(str(item["notes"]))}"></td>
    </tr>
    """


def _instrument_row_html(item: dict) -> str:
    cells = [
        item["sequence"],
        item["tag"],
        item["instrument_type"],
        item["source"],
        item["block_name"],
        item["layer"],
        item["x"],
        item["y"],
        item["confidence"],
        item["notes"],
    ]
    return "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in cells) + "</tr>"


def _error_page(message: str) -> str:
    return _layout(
        f"""
        <section class="error">
          <h1>Nao foi possivel concluir</h1>
          <p>{html.escape(message)}</p>
          <a class="button" href="/">Voltar</a>
        </section>
        """
    )


def _layout(content: str, active: str = "dashboard") -> str:
    dashboard_active = "active" if active == "dashboard" else ""
    symbols_active = "active" if active == "symbols" else ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>InstrumentaCAD</title>
  <style>
    :root {{
      --bg: #eef3f7;
      --panel: #ffffff;
      --ink: #111827;
      --muted: #5f6c7b;
      --line: #d4dde8;
      --brand: #0f766e;
      --brand-dark: #115e59;
      --accent: #f59e0b;
      --soft: #dff5f1;
      --steel: #1f2937;
      --warn: #a35a00;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; background: var(--bg); color: var(--ink); }}
    body::before {{ content: ""; position: fixed; inset: 0; z-index: -1; background: linear-gradient(135deg, rgba(31, 41, 55, 0.08), transparent 42%), linear-gradient(0deg, rgba(15, 118, 110, 0.07), transparent 34%); }}
    header {{ background: rgba(255, 255, 255, 0.96); border-bottom: 1px solid var(--line); position: sticky; top: 0; z-index: 10; backdrop-filter: blur(10px); }}
    .nav {{ width: min(1220px, calc(100% - 32px)); margin: 0 auto; min-height: 68px; display: flex; align-items: center; justify-content: space-between; gap: 18px; }}
    .brand {{ display: inline-flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 900; color: var(--ink); text-decoration: none; letter-spacing: 0; }}
    .brand::before {{ content: ""; width: 28px; height: 28px; border-radius: 7px; background: linear-gradient(135deg, var(--brand), var(--steel)); box-shadow: inset 0 0 0 2px rgba(255,255,255,0.35); }}
    nav {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    nav a {{ padding: 10px 12px; border-radius: 7px; color: var(--muted); text-decoration: none; font-weight: 700; }}
    nav a.active, nav a:hover {{ background: var(--soft); color: var(--brand); }}
    main {{ width: min(1220px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 44px; }}
    h1, h2, p {{ margin-top: 0; }}
    h1 {{ max-width: 780px; margin-bottom: 14px; font-size: 54px; line-height: 1.02; letter-spacing: 0; }}
    h2 {{ margin-bottom: 6px; font-size: 24px; }}
    .lead {{ max-width: 690px; color: var(--muted); font-size: 17px; line-height: 1.55; }}
    .eyebrow {{ margin-bottom: 10px; color: var(--brand); font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0.08em; }}
    .hero-app {{ display: grid; grid-template-columns: minmax(0, 1fr) 470px; gap: 28px; align-items: center; min-height: 560px; padding: 28px 0; }}
    .hero-copy {{ padding: 42px; min-height: 430px; color: white; background: linear-gradient(135deg, #1f2937, #0f766e); border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; box-shadow: 0 20px 50px rgba(17, 24, 39, 0.18); }}
    .hero-copy .eyebrow, .hero-copy .lead {{ color: rgba(255,255,255,0.82); }}
    .hero-actions {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 24px 0; }}
    .process-strip {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 28px; }}
    .process-strip span {{ padding: 10px; border: 1px solid rgba(255,255,255,0.22); border-radius: 7px; background: rgba(255,255,255,0.08); font-size: 12px; font-weight: 800; }}
    .upload-panel, .panel, .success, .error, .symbols-card {{ display: grid; gap: 14px; padding: 22px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 16px 34px rgba(21, 32, 43, 0.08); }}
    .form-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .form-grid.three {{ grid-template-columns: repeat(3, 1fr); }}
    label {{ display: grid; gap: 6px; font-size: 13px; font-weight: 800; color: #334155; }}
    input, select {{ width: 100%; min-height: 42px; padding: 10px 11px; border: 1px solid #cbd5e1; border-radius: 7px; background: white; color: var(--ink); }}
    input[type="file"] {{ padding: 12px; border-style: dashed; background: #fbfdff; }}
    input[type="checkbox"] {{ width: 18px; min-height: 18px; }}
    button, .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0 16px; border: 0; border-radius: 7px; background: var(--brand); color: white; font-weight: 800; text-decoration: none; cursor: pointer; }}
    button:hover, .button:hover {{ background: var(--brand-dark); }}
    .button.secondary {{ background: var(--soft); color: var(--brand); }}
    .hero-copy .button.secondary {{ background: rgba(255,255,255,0.12); color: white; border: 1px solid rgba(255,255,255,0.3); }}
    .hint {{ margin: 0; color: var(--muted); font-size: 13px; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 28px; }}
    .stats div {{ padding: 20px; background: var(--panel); border: 1px solid var(--line); border-left: 5px solid var(--brand); border-radius: 8px; }}
    .stats strong {{ display: block; margin: 7px 0; font-size: 36px; color: var(--steel); }}
    .stats span {{ color: var(--muted); font-weight: 800; font-size: 12px; text-transform: uppercase; }}
    .stats small {{ color: var(--muted); }}
    .section-head, .result-header {{ display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 16px; }}
    .page-title {{ margin-bottom: 22px; }}
    .workspace-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 18px; align-items: start; }}
    .symbols-card {{ align-content: start; }}
    .symbols-card p {{ color: var(--muted); line-height: 1.5; }}
    .symbols-card dl {{ display: grid; gap: 10px; margin: 0; }}
    .symbols-card dl div {{ padding: 12px; border-radius: 7px; background: #f7fafc; border: 1px solid var(--line); }}
    .symbols-card dt {{ color: var(--steel); font-weight: 900; margin-bottom: 4px; }}
    .symbols-card dd {{ margin: 0; color: var(--muted); font-size: 13px; }}
    .table-wrap {{ overflow-x: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    table {{ width: 100%; min-width: 980px; border-collapse: collapse; }}
    th, td {{ padding: 11px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ background: #233142; color: white; font-size: 12px; text-transform: uppercase; }}
    td input {{ min-width: 140px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .summary {{ min-width: 310px; margin: 0; padding: 0; list-style: none; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    .summary li {{ display: flex; justify-content: space-between; gap: 14px; padding: 12px 14px; border-bottom: 1px solid var(--line); }}
    .summary li:last-child {{ border-bottom: 0; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .footer-actions {{ justify-content: flex-end; margin-top: 18px; }}
    .empty {{ padding: 24px; text-align: center; color: var(--muted); }}
    .inline-form {{ margin: 0; }}
    .link-button {{ min-height: auto; padding: 0; background: transparent; color: #b42318; }}
    .link-button:hover {{ background: transparent; color: #7a1b12; }}
    .success {{ margin-bottom: 18px; }}
    .error {{ margin-top: 30px; }}
    @media (max-width: 900px) {{
      h1 {{ font-size: 34px; }}
      .hero-app, .form-grid, .form-grid.three, .stats, .workspace-grid, .process-strip {{ grid-template-columns: 1fr; }}
      .hero-copy {{ padding: 24px; min-height: 0; }}
      .section-head, .result-header {{ align-items: stretch; flex-direction: column; }}
      .summary {{ min-width: 0; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="nav">
      <a class="brand" href="/">InstrumentaCAD</a>
      <nav>
        <a class="{dashboard_active}" href="/">Dashboard</a>
        <a class="{symbols_active}" href="/symbols">Biblioteca de simbolos</a>
      </nav>
    </div>
  </header>
  <main>{content}</main>
</body>
</html>"""


def _parse_form(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    if "multipart/form-data" in content_type:
        return _parse_multipart(body, content_type)
    parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}, {}


def _parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    message_bytes = b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = BytesParser(policy=policy.default).parsebytes(message_bytes)

    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[name] = (Path(filename).name, payload)
        else:
            fields[name] = payload.decode("utf-8", errors="ignore")

    return fields, files


def _project_from_fields(fields: dict[str, str]) -> dict[str, str]:
    return {
        "project_name": fields.get("project_name", "").strip(),
        "client_name": fields.get("client_name", "").strip(),
        "technical_owner": fields.get("technical_owner", "").strip(),
        "drawing_type": fields.get("drawing_type", "").strip(),
    }


def _save_logo(upload_id: str, upload: tuple[str, bytes] | None) -> Path | None:
    if upload is None:
        return None
    filename, data = upload
    extension = Path(filename).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg"} or not data:
        return None
    logo_path = UPLOAD_DIR / f"{upload_id}-logo{extension}"
    logo_path.write_bytes(data)
    return logo_path


def _reviewed_instruments(fields: dict[str, str]) -> list[dict]:
    instruments = []
    count = int(fields.get("count", "0") or 0)
    for index in range(count):
        if fields.get(f"use_{index}") != "1":
            continue
        sequence = len(instruments) + 1
        instruments.append(
            {
                "sequence": sequence,
                "tag": fields.get(f"tag_{index}", "").strip() or f"SEM-TAG-{sequence:03d}",
                "instrument_type": fields.get(f"type_{index}", "").strip() or "Instrumento nao classificado",
                "source": fields.get(f"source_{index}", ""),
                "block_name": fields.get(f"block_{index}", "-"),
                "layer": fields.get(f"layer_{index}", "0"),
                "x": _to_float(fields.get(f"x_{index}", "0")),
                "y": _to_float(fields.get(f"y_{index}", "0")),
                "confidence": fields.get(f"confidence_{index}", "Media"),
                "notes": fields.get(f"notes_{index}", "").strip() or "OK",
            }
        )
    return instruments


def _summary_by_type(instruments: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in instruments:
        instrument_type = item.get("instrument_type") or "Instrumento"
        summary[instrument_type] = summary.get(instrument_type, 0) + 1
    return dict(sorted(summary.items()))


def _warnings_for_reviewed(instruments: list[dict]) -> list[str]:
    warnings = []
    if not instruments:
        warnings.append("Nenhum instrumento foi aprovado na revisao.")
    if any(str(item.get("tag", "")).startswith("SEM-TAG") for item in instruments):
        warnings.append("Alguns instrumentos foram mantidos sem tag definitiva.")
    if any(item.get("instrument_type") == "Instrumento nao classificado" for item in instruments):
        warnings.append("Existem instrumentos sem classificacao definitiva.")
    return warnings


def _to_float(value: str) -> float:
    try:
        return round(float(value), 3)
    except ValueError:
        return 0.0


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", filename).strip("-")
    return cleaned or "arquivo.dxf"


def run(port: int = 8000) -> None:
    ensure_storage()
    UPLOAD_DIR.mkdir(exist_ok=True)
    REPORT_DIR.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), CadInstrumentationHandler)
    print(f"Servidor iniciado em http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="InstrumentaCAD")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    run(args.port)
