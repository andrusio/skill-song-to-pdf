# /// script
# requires-python = ">=3.10"
# dependencies = ["reportlab>=4"]
# ///
"""Convierte una hoja de acordes en texto (acordes encima de la letra) a PDF.

Formato de entrada (ver SKILL.md):
    # Título: ...        <- cabecera (líneas "# clave: valor" al principio)
    [Estribillo]         <- marca de sección
    Am        F          <- línea de acordes (se detecta sola)
    letra de la canción  <- línea de letra
    // comentario        <- no se imprime

Uso:
    uv run render_pdf.py cancion.txt -o cancion.pdf [--transponer N] [--capo N]
                                     [--columnas auto|1|2] [--tamano 10]
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chordlib  # noqa: E402

from reportlab.lib.colors import HexColor  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

CHORD_COLOR = HexColor("#B4261A")
SECTION_COLOR = HexColor("#1F4E79")
META_COLOR = HexColor("#555555")
FONT, FONT_B = "Courier", "Courier-Bold"

ASCII_FIXES = {"‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
               "–": "-", "—": "-", " ": " "}


def latin1(s: str) -> str:
    """Las fuentes base del PDF solo cubren Latin-1 (suficiente para español)."""
    s = "".join(ASCII_FIXES.get(c, c) for c in s)
    out = []
    for c in s:
        try:
            c.encode("latin-1")
            out.append(c)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", c).encode("latin-1", "ignore").decode("latin-1")
            out.append(base or "?")
    return "".join(out)


def parse_sheet(text: str):
    meta, body = {}, []
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        ln = lines[i].lstrip("#").strip()
        if ":" in ln:
            k, v = ln.split(":", 1)
            meta[k.strip().lower()] = v.strip()
        i += 1
    for ln in lines[i:]:
        if ln.lstrip().startswith("//"):
            continue
        ln = ln.rstrip().expandtabs(4)
        s = ln.strip()
        if not s:
            kind = "blank"
        elif s.startswith("[") and s.endswith("]"):
            kind = "section"
        elif chordlib.is_chord_line(ln):
            kind = "chords"
        else:
            kind = "lyric"
        body.append((kind, ln))
    return meta, body


def blocks_of(body):
    """Agrupa en párrafos (separados por líneas en blanco) para no partirlos entre columnas."""
    blocks, cur = [], []
    for kind, ln in body:
        if kind == "blank":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append((kind, ln))
    if cur:
        blocks.append(cur)
    return blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("txt")
    ap.add_argument("-o", "--output")
    ap.add_argument("--transponer", type=int, default=0, help="semitonos (+/-) para cambiar de tono")
    ap.add_argument("--capo", type=int, default=None,
                    help="traste de cejilla: escribe las posiciones relativas a la cejilla")
    ap.add_argument("--columnas", default="auto", choices=["auto", "1", "2"])
    ap.add_argument("--tamano", type=float, default=None, help="tamaño de letra (pt); por defecto se ajusta solo")
    args = ap.parse_args()

    src = Path(args.txt)
    meta, body = parse_sheet(src.read_text(encoding="utf-8"))
    out = Path(args.output) if args.output else src.with_suffix(".pdf")

    # --- transposición / cejilla ------------------------------------------
    key = meta.get("tono", "").split()[0] if meta.get("tono") else ""
    # Los acordes del .txt son siempre los que suenan; la cejilla solo se aplica al imprimir.
    capo = args.capo or 0
    shift = args.transponer - capo
    sounding_key = key
    if key and args.transponer:
        sounding_key = chordlib.transpose_key(key, args.transponer)
    shape_key = chordlib.transpose_key(sounding_key, -capo) if (sounding_key and capo) else sounding_key
    if shift:
        flats = chordlib.key_uses_flats(shape_key) if shape_key else False
        body = [(k, chordlib.transpose_line(l, shift, flats) if k == "chords" else l) for k, l in body]
    if sounding_key:
        meta["tono"] = sounding_key + (f"  (formas de {shape_key})" if capo else "")
    meta["capo"] = str(capo)

    # --- maquetación -------------------------------------------------------
    W, H = A4
    margin = 15 * mm
    title = latin1(meta.get("título") or meta.get("titulo") or src.stem)
    artist = latin1(meta.get("artista", ""))
    header_h = 22 * mm
    usable_w = W - 2 * margin
    usable_h = H - 2 * margin
    max_chars = max((len(l) for _, l in body), default=40)
    gutter = 8 * mm
    char_w = stringWidth("M", FONT, 1)  # ancho por punto de tamaño

    def fit_size(cols):
        col_w = (usable_w - gutter * (cols - 1)) / cols
        return min(11.0, col_w / (max_chars * char_w))

    n_lines = len(body)
    if args.columnas == "auto":
        one = args.tamano or fit_size(1)
        lines_per_page = (usable_h - header_h) / (one * 1.25)
        two = args.tamano or fit_size(2)
        cols = 2 if (n_lines > lines_per_page and two >= 8.0) else 1
    else:
        cols = int(args.columnas)
    size = args.tamano or max(6.5, fit_size(cols))
    lead = size * 1.25
    col_w = (usable_w - gutter * (cols - 1)) / cols

    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle(f"{title} - {artist}" if artist else title)
    page = 1

    def draw_header(first: bool):
        y = H - margin
        if first:
            c.setFont("Helvetica-Bold", 18)
            c.drawString(margin, y - 14, title)
            c.setFont("Helvetica", 12)
            c.setFillColor(META_COLOR)
            if artist:
                c.drawString(margin, y - 30, artist)
            info = []
            if meta.get("tono"):
                info.append(f"Tono: {meta['tono']}")
            if capo:
                info.append(f"Cejilla: traste {capo}")
            if meta.get("tempo"):
                info.append(f"Tempo: {meta['tempo']}")
            c.setFont("Helvetica", 9.5)
            c.drawRightString(W - margin, y - 14, latin1("   ".join(info)))
            c.setFillColor(HexColor("#000000"))
            c.setLineWidth(0.5)
            c.line(margin, y - 38, W - margin, y - 38)
            return y - 38 - lead * 1.3
        c.setFont("Helvetica", 8)
        c.setFillColor(META_COLOR)
        c.drawString(margin, y - 6, f"{title} - {artist}".strip(" -"))
        c.setFillColor(HexColor("#000000"))
        return y - 6 - lead * 1.3

    top = draw_header(True)
    col, y = 0, top

    def footer():
        c.setFont("Helvetica", 8)
        c.setFillColor(META_COLOR)
        c.drawRightString(W - margin, margin / 2, str(page))
        c.setFillColor(HexColor("#000000"))

    def next_column():
        nonlocal col, y, top, page
        col += 1
        if col >= cols:
            footer()
            c.showPage()
            page += 1
            top = draw_header(False)
            col = 0
        y = top

    for block in blocks_of(body):
        block_h = len(block) * lead
        if y - block_h < margin and y != top:
            if block_h <= top - margin:
                next_column()
        x0 = margin + col * (col_w + gutter)
        for kind, ln in block:
            if y - lead < margin - lead * 0.3:
                next_column()
                x0 = margin + col * (col_w + gutter)
            if kind == "section":
                c.setFont(FONT_B, size)
                c.setFillColor(SECTION_COLOR)
            elif kind == "chords":
                c.setFont(FONT_B, size)
                c.setFillColor(CHORD_COLOR)
            else:
                c.setFont(FONT, size)
                c.setFillColor(HexColor("#000000"))
            c.drawString(x0, y, latin1(ln))
            y -= lead
        y -= lead * 0.6  # separación entre párrafos
    footer()
    c.save()
    print(out)


if __name__ == "__main__":
    main()
