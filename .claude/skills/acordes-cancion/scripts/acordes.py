# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "yt-dlp[default]",
#   "faster-whisper>=1.0",
#   "madmom @ git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae",
#   "numpy<2",
#   "scipy",
#   "requests",
#   "secretstorage; sys_platform == 'linux'",
# ]
# ///
"""Genera una hoja de acordes (acordes encima de la letra) a partir de un link
de YouTube o de un archivo de audio.

Pasos: audio -> letra (LRCLIB sincronizada / archivo / Whisper) -> tiempos por
palabra (Whisper) -> acordes y pulsos (madmom) -> cancion.txt editable.

Uso:
    uv run acordes.py <url-o-archivo> [-o DIR] [--artista A] [--titulo T]
                      [--letra-archivo letra.txt] [--idioma es]
                      [--modelo-whisper small] [--sin-whisper] [--sin-lrclib]

Salida en DIR/<artista - titulo>/:
    cancion.txt   hoja de acordes editable (entrada de render_pdf.py)
    datos.json    datos crudos con tiempos (acordes, palabras, líneas)
    audio.wav     audio usado
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chordlib  # noqa: E402


def log(msg: str):
    print(f"[acordes] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 1. Audio y metadatos
# ---------------------------------------------------------------------------

NOISE_RE = re.compile(
    r"official|oficial|video|v[ií]deo|lyric|letra|audio|hd|4k|remaster|visualizer|clip|"
    r"live|en vivo|directo|ac[uú]stic|version|versi[oó]n|mv|m/v", re.I)


def clean_title(raw: str) -> str:
    s = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", raw)   # quita (Official Video), [HD], ...
    s = re.sub(r"\s(ft\.?|feat\.?|featuring)\s.*$", " ", s, flags=re.I)
    s = re.sub(r"\|.*$", " ", s)
    s = re.sub(r"\s+-\s+(" + NOISE_RE.pattern + r").*$", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -_\"'")


def split_artist_title(raw: str, fallback_artist: str = ""):
    s = clean_title(raw)
    for sep in (" - ", " – ", " — ", " -", "- "):
        if sep in s:
            a, t = s.split(sep, 1)
            return a.strip(), t.strip()
    artist = re.sub(r"\s*(- Topic|VEVO|Oficial|Official)$", "", fallback_artist or "", flags=re.I).strip()
    return artist, s


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s)) or s.startswith("www.")


COOKIE_SPEC = re.compile(
    r"(?P<name>[^+:]+)(?:\+(?P<keyring>[^:]+))?(?::(?!:)(?P<profile>.+?))?(?:::(?P<container>.+))?")


def parse_cookie_spec(spec: str):
    """Misma sintaxis que yt-dlp: NAVEGADOR[+LLAVERO][:PERFIL][::CONTENEDOR].
    Ej.: 'firefox', 'chrome:Profile 1', 'chrome+gnomekeyring:Profile 1'."""
    m = COOKIE_SPEC.fullmatch(spec.strip())
    if not m:
        sys.exit(f"--cookies-navegador inválido: {spec!r} (formato: NAVEGADOR[+LLAVERO][:PERFIL])")
    keyring = m.group("keyring").upper() if m.group("keyring") else None
    return (m.group("name").lower(), m.group("profile"), keyring, m.group("container"))


def ensure_dbus_session():
    """En Linux, Chrome/Chromium cifran las cookies con el llavero del escritorio, al
    que se accede por D-Bus. Si el agente corre fuera de la sesión gráfica, apuntar
    al bus del usuario."""
    import os

    if sys.platform.startswith("linux") and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = Path(f"/run/user/{os.getuid()}/bus")
        if bus.exists():
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"


def download_youtube(url: str, workdir: Path, cookies_browser: str | None = None) -> dict:
    import yt_dlp

    workdir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": str(workdir / "descarga.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    if cookies_browser:
        ensure_dbus_session()
        opts["cookiesfrombrowser"] = parse_cookie_spec(cookies_browser)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        hint = ""
        if "not a bot" in msg or "Sign in" in msg:
            hint = ("YouTube pide verificación anti-bot. Reintentar con --cookies-navegador "
                    "(p. ej. firefox, o 'chrome+gnomekeyring:Profile 1'), o pasar el audio como archivo.")
        sys.exit(f"ERROR al descargar: {msg}\n{hint}".rstrip())
    src = workdir / "descarga.wav"
    return {
        "audio": src,
        "raw_title": info.get("title") or "",
        "artist": info.get("artist") or (info.get("artists") or [None])[0] or "",
        "track": info.get("track") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "source": info.get("webpage_url") or url,
    }


def probe_tags(path: Path) -> dict:
    try:
        out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
                             capture_output=True, text=True, check=True).stdout
        fmt = json.loads(out).get("format", {})
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        return {"artist": tags.get("artist", ""), "track": tags.get("title", ""),
                "duration": float(fmt.get("duration", 0) or 0)}
    except Exception:
        return {"artist": "", "track": "", "duration": None}


def to_wav(src: Path, dst: Path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "44100", str(dst)],
                   check=True)


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip()
    return re.sub(r"[\s]+", " ", s)[:80] or "cancion"


# ---------------------------------------------------------------------------
# 2. Letra
# ---------------------------------------------------------------------------

LRC_LINE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$")


def parse_lrc(text: str):
    lines = []
    for raw in text.splitlines():
        m = LRC_LINE.match(raw.strip())
        if not m:
            continue
        t = int(m.group(1)) * 60 + float(m.group(2))
        lines.append({"text": m.group(3).strip(), "start": t})
    return lines


def parse_plain(text: str):
    return [{"text": ln.strip(), "start": None} for ln in text.splitlines()]


def fetch_lrclib(artist: str, title: str, duration: float | None):
    import requests

    headers = {"User-Agent": "acordes-cancion-skill/1.0 (https://github.com)"}
    base = "https://lrclib.net/api/search"
    queries = []
    if artist and title:
        queries.append({"artist_name": artist, "track_name": title})
    queries.append({"q": f"{artist} {title}".strip()})
    if title:
        queries.append({"track_name": title})
    for q in queries:
        try:
            r = requests.get(base, params=q, headers=headers, timeout=20)
            r.raise_for_status()
            results = r.json()
        except Exception as e:  # sin red, API caída, etc.
            log(f"LRCLIB falló ({e})")
            return None
        if not results:
            continue

        def score(x):
            d = abs((x.get("duration") or 0) - (duration or 0)) if duration else 0
            synced_bonus = 0 if x.get("syncedLyrics") else 30
            name_pen = 0 if norm(x.get("artistName", "")) and norm(artist) and \
                norm(artist) in norm(x.get("artistName", "")) else 10
            return d + synced_bonus + (name_pen if artist else 0)

        results = [x for x in results if x.get("syncedLyrics") or x.get("plainLyrics")]
        if not results:
            continue
        best = min(results, key=score)
        if duration and abs((best.get("duration") or 0) - duration) > 20 and best.get("syncedLyrics"):
            log(f"LRCLIB: la duración no coincide ({best.get('duration')} vs {duration:.0f} s); "
                "se usan solo las palabras, no los tiempos")
            return {"lines": parse_plain(best.get("plainLyrics") or re.sub(r"\[[^\]]*\]", "", best["syncedLyrics"])),
                    "source": "lrclib (sin tiempos)", "match": f"{best['artistName']} - {best['trackName']}"}
        names = {"match": f"{best['artistName']} - {best['trackName']}",
                 "artist": best["artistName"], "title": best["trackName"]}
        if best.get("syncedLyrics"):
            return {"lines": parse_lrc(best["syncedLyrics"]), "source": "lrclib", **names}
        return {"lines": parse_plain(best["plainLyrics"]), "source": "lrclib (sin tiempos)", **names}
    return None


# ---------------------------------------------------------------------------
# 3. Whisper: tiempos por palabra
# ---------------------------------------------------------------------------

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9']+", "", s)


def load_audio_16k(wav: Path):
    """Decodifica con ffmpeg (evita depender de la versión de PyAV que usa faster-whisper)."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), "-f", "f32le", "-ac", "1",
                          "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def run_whisper(wav: Path, model_name: str, language: str | None, initial_prompt: str | None):
    from faster_whisper import WhisperModel

    audio = load_audio_16k(wav)
    attempts = [("cuda", "float16"), ("cpu", "int8")]
    last_err = None
    for device, ctype in attempts:
        try:
            model = WhisperModel(model_name, device=device, compute_type=ctype)
            segs, info = model.transcribe(
                audio, language=language, word_timestamps=True, vad_filter=False,
                condition_on_previous_text=False, initial_prompt=initial_prompt,
            )
            words, segments = [], []
            for s in segs:
                sw = [{"text": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                      for w in (s.words or []) if w.word.strip()]
                words.extend(sw)
                segments.append({"start": float(s.start), "end": float(s.end), "words": sw})
            log(f"Whisper ({model_name}, {device}): {len(words)} palabras, idioma {info.language}")
            return {"words": words, "segments": segments, "language": info.language}
        except Exception as e:
            last_err = e
            log(f"Whisper en {device} no disponible ({type(e).__name__}: {str(e)[:120]})")
    raise RuntimeError(f"Whisper falló: {last_err}")


STOPWORDS = {
    "es": {"de", "que", "el", "la", "y", "en", "no", "me", "te", "lo", "un", "una", "por", "con", "mi", "tu", "yo", "es"},
    "en": {"the", "and", "you", "i", "to", "a", "of", "in", "my", "me", "it", "is", "your", "on", "be", "we"},
    "pt": {"de", "que", "e", "o", "a", "não", "eu", "você", "um", "uma", "meu", "com", "se", "do", "da", "pra"},
}


def guess_language(text: str):
    words = re.findall(r"[a-záéíóúñãõç']+", text.lower())
    if len(words) < 15:
        return None
    scores = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
    lang = max(scores, key=scores.get)
    return lang if scores[lang] >= 0.12 * len(words) else None


def lines_from_whisper(segments, max_words=9, pause=0.6):
    """Sin letra externa: cortar la transcripción en líneas cortas."""
    lines = []
    for seg in segments:
        cur = []
        for w in seg["words"]:
            if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > pause):
                lines.append(cur)
                cur = []
            cur.append(w)
        if cur:
            lines.append(cur)
    out = []
    for i, ws in enumerate(lines):
        text = " ".join(w["text"] for w in ws)
        out.append({"text": text, "start": ws[0]["start"]})
    return out


# ---------------------------------------------------------------------------
# 4. Alineación letra <-> tiempos
# ---------------------------------------------------------------------------

SEC_PER_CHAR = 0.075


def tokenize_line(text: str):
    """Devuelve [(palabra, offset_caracter)]."""
    return [(m.group(0), m.start()) for m in re.finditer(r"\S+", text)]


def align(lyric_lines, whisper_words, audio_dur):
    """Asigna start/end a cada palabra de cada línea. Modifica lyric_lines."""
    flat = []  # (idx_linea, idx_palabra, norm)
    for li, ln in enumerate(lyric_lines):
        ln["words"] = [{"text": w, "char": c, "start": None, "end": None} for w, c in tokenize_line(ln["text"])]
        for wi, w in enumerate(ln["words"]):
            n = norm(w["text"])
            if n:
                flat.append((li, wi, n))

    if whisper_words:
        wn = [norm(w["text"]) for w in whisper_words]
        sm = difflib.SequenceMatcher(None, [f[2] for f in flat], wn, autojunk=False)
        for a, b, size in sm.get_matching_blocks():
            for k in range(size):
                li, wi, _ = flat[a + k]
                ww = whisper_words[b + k]
                ln = lyric_lines[li]
                # Si la letra trae tiempo de línea (LRC) y Whisper se aleja mucho, desconfiar de Whisper.
                if ln.get("start") is not None and abs(ww["start"] - ln["start"]) > 4.0 and wi < 3:
                    continue
                ln["words"][wi]["start"], ln["words"][wi]["end"] = ww["start"], ww["end"]

    # Inicio de cada línea: LRC si existe; si no, primera palabra con tiempo.
    for ln in lyric_lines:
        if ln.get("start") is None:
            known = [w for w in ln["words"] if w["start"] is not None]
            if known:
                k = known[0]
                ln["start"] = max(0.0, k["start"] - SEC_PER_CHAR * k["char"])
        elif ln["words"] and ln["words"][0]["start"] is not None:
            # Whisper suele ser más preciso para el arranque real de la voz.
            if abs(ln["words"][0]["start"] - ln["start"]) < 1.5:
                ln["start"] = min(ln["start"], ln["words"][0]["start"]) if ln["words"][0]["start"] < ln["start"] \
                    else ln["words"][0]["start"]

    # Líneas sin ningún tiempo: interpolar entre vecinas.
    idx = [i for i, ln in enumerate(lyric_lines) if ln.get("start") is not None]
    if not idx:
        t = 0.0
        for ln in lyric_lines:
            ln["start"] = t
            t += max(2.0, SEC_PER_CHAR * len(ln["text"]))
    else:
        for i, ln in enumerate(lyric_lines):
            if ln.get("start") is not None:
                continue
            prev = max([j for j in idx if j < i], default=None)
            nxt = min([j for j in idx if j > i], default=None)
            if prev is not None and nxt is not None:
                t0, t1 = lyric_lines[prev]["start"], lyric_lines[nxt]["start"]
                ln["start"] = t0 + (t1 - t0) * (i - prev) / (nxt - prev)
            elif prev is not None:
                ln["start"] = lyric_lines[prev]["start"] + 3.0 * (i - prev)
            else:
                ln["start"] = max(0.0, lyric_lines[nxt]["start"] - 3.0 * (nxt - i))

    # Tiempos de palabras que faltan: interpolar por posición de carácter.
    for i, ln in enumerate(lyric_lines):
        words = ln["words"]
        if not words:
            ln["end"] = ln["start"]
            continue
        next_start = next((lyric_lines[j]["start"] for j in range(i + 1, len(lyric_lines))
                           if lyric_lines[j]["words"]), audio_dur)
        if words[0]["start"] is None:
            words[0]["start"] = ln["start"]
        last = words[-1]
        if last["start"] is None:
            est = ln["start"] + SEC_PER_CHAR * len(ln["text"])
            last["start"] = min(est, next_start - 0.2)
        anchors = [k for k, w in enumerate(words) if w["start"] is not None]
        for a, b in zip(anchors, anchors[1:]):
            ca, cb = words[a]["char"], words[b]["char"]
            ta, tb = words[a]["start"], words[b]["start"]
            for k in range(a + 1, b):
                frac = (words[k]["char"] - ca) / max(1, cb - ca)
                words[k]["start"] = ta + (tb - ta) * frac
        for k, w in enumerate(words):  # forzar monotonía
            if k and w["start"] < words[k - 1]["start"]:
                w["start"] = words[k - 1]["start"]
        if last["end"] is None:
            last["end"] = min(last["start"] + 0.6, next_start)
        ln["end"] = max(last["end"], last["start"])
        ln["start"] = min(ln["start"], words[0]["start"])
    return lyric_lines


# ---------------------------------------------------------------------------
# 5. Acordes y pulsos (madmom)
# ---------------------------------------------------------------------------

def detect_chords(wav: Path):
    from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor

    feats = CNNChordFeatureProcessor()(str(wav))
    segs = CRFChordRecognitionProcessor()(feats)
    out = []
    for start, end, label in segs:
        out.append({"start": float(start), "end": float(end), "chord": madmom_label(str(label))})
    return out


def detect_beats(wav: Path):
    from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
    import numpy as np

    act = RNNBeatProcessor()(str(wav))
    beats = DBNBeatTrackingProcessor(fps=100)(act)
    bpm = None
    if len(beats) > 4:
        bpm = float(60.0 / np.median(np.diff(beats)))
    return [float(b) for b in beats], bpm


def madmom_label(label: str) -> str:
    if label in ("N", "X"):
        return "N"
    root, _, qual = label.partition(":")
    return root + ("m" if qual.startswith("min") else "")


def clean_chords(chords, beats, key):
    """Ajusta cambios al pulso más cercano, elimina acordes fugaces y unifica deletreo."""
    flats = chordlib.key_uses_flats(key)
    beat_len = None
    if len(beats) > 4:
        import numpy as np
        beat_len = float(np.median(np.diff(beats)))
        bs = np.array(beats)
        for c in chords:
            j = int(np.argmin(abs(bs - c["start"])))
            if abs(bs[j] - c["start"]) < 0.35 * beat_len:
                c["start"] = float(bs[j])
    min_dur = max(0.45, 0.9 * beat_len) if beat_len else 0.6
    # recomputar fines tras el ajuste
    chords = sorted(chords, key=lambda c: c["start"])
    for a, b in zip(chords, chords[1:]):
        a["end"] = b["start"]
    changed = True
    while changed:
        changed = False
        for i, c in enumerate(chords):
            if c["end"] - c["start"] < min_dur and len(chords) > 1:
                # absorber en el vecino más largo
                if i == 0:
                    chords[1]["start"] = c["start"]
                elif i == len(chords) - 1 or (chords[i - 1]["end"] - chords[i - 1]["start"]) >= \
                        (chords[i + 1]["end"] - chords[i + 1]["start"]):
                    chords[i - 1]["end"] = c["end"]
                else:
                    chords[i + 1]["start"] = c["start"]
                del chords[i]
                changed = True
                break
        # fusionar repetidos consecutivos
        merged = []
        for c in chords:
            if merged and merged[-1]["chord"] == c["chord"]:
                merged[-1]["end"] = c["end"]
            else:
                merged.append(c)
        if len(merged) != len(chords):
            changed = True
        chords = merged
    for c in chords:
        if c["chord"] != "N":
            c["chord"] = chordlib.transpose_chord(c["chord"], 0, flats)
    return chords


# ---------------------------------------------------------------------------
# 6. Maquetación de la hoja
# ---------------------------------------------------------------------------

LEAD_IN = 0.5        # un cambio de acorde hasta 0.5 s antes de la línea se escribe al principio
INSTR_GAP = 4.0      # huecos sin voz más largos que esto generan una línea instrumental
PARA_GAP = 3.0       # hueco que separa párrafos


def chord_at(chords, t):
    for c in chords:
        if c["start"] <= t < c["end"]:
            return c["chord"]
    return "N"


def compress(seq):
    """Compacta progresiones que se repiten: 'Bm   G   D   A   (x6)'.
    Tolera algún acorde mal detectado (autocorrelación >= 70%); en ese caso añade
    un comentario // con la secuencia completa para que el revisor decida."""
    if not seq:
        return ""
    n = len(seq)
    full = "   ".join(seq)
    for p in range(2, 9):
        if n < 2 * p:
            break
        r = sum(seq[i] == seq[i + p] for i in range(n - p)) / (n - p)
        if r < 0.7:
            continue
        # patrón = ventana de longitud p más frecuente (búsqueda sin solapamiento)
        counts = {}
        for k in range(n - p + 1):
            pat = tuple(seq[k:k + p])
            if pat in counts:
                continue
            c, i = 0, 0
            while i <= n - p:
                if tuple(seq[i:i + p]) == pat:
                    c += 1
                    i += p
                else:
                    i += 1
            counts[pat] = c
        pat = max(counts, key=lambda q: counts[q])  # max() conserva el primero en caso de empate
        times = round(n / p)
        line = "   ".join(pat) + f"   (x{times})"
        if r < 0.999 or n % p:
            line += "\n// secuencia detectada completa: " + full
        return line
    rows = [seq[i:i + 8] for i in range(0, n, 8)]
    return "\n".join("   ".join(r) for r in rows)


def chord_seq(chords, t0, t1, min_inside=0.8):
    seq = []
    for c in chords:
        a, b = max(c["start"], t0), min(c["end"], t1)
        if b - a >= min_inside and c["chord"] != "N":
            if not seq or seq[-1] != c["chord"]:
                seq.append(c["chord"])
    return seq


def build_line(line, chords, t0, t1, prev_chord=None):
    """Línea de acordes encima de una línea de letra. Cambios en [t0, t1)."""
    words = line["words"]
    placed = []  # (pos, chord)
    # Acorde que suena al empezar la línea (salvo que cambie enseguida).
    active = chord_at(chords, line["start"] + 0.05)
    changes_soon = any(line["start"] - LEAD_IN <= c["start"] <= line["start"] + 0.45 and c["chord"] != "N"
                       for c in chords)
    # No repetirlo si ya quedó escrito al final de la línea anterior del mismo bloque.
    if active != "N" and not changes_soon and active != prev_chord:
        placed.append((0, active))
    for c in chords:
        t = c["start"]
        if not (t0 <= t < t1) or c["chord"] == "N":
            continue
        if t <= line["start"] + 0.45:
            pos = 0
        else:
            pos = 0
            for w in words:
                if w["start"] is not None and w["start"] <= t + 0.15:
                    pos = w["char"]
            # Si el cambio cae después de la última palabra, ponerlo al final de la línea.
            if words and t > (line.get("end") or 0) + 0.3:
                pos = len(line["text"]) + 1
        if placed and placed[-1][0] == pos:
            placed[-1] = (pos, c["chord"])  # el último cambio sobre la misma palabra manda
        elif placed and placed[-1][1] == c["chord"]:
            continue
        else:
            placed.append((pos, c["chord"]))
    # evitar solapamientos
    buf, cursor = "", 0
    for pos, ch in placed:
        pos = max(pos, cursor + (1 if cursor else 0))
        buf += " " * (pos - len(buf)) + ch
        cursor = len(buf)
    return buf.rstrip()


def build_sheet(meta, lines, chords, audio_dur):
    out = []
    # quitar líneas vacías de LRC pero recordar dónde había cortes
    breaks_before = set()
    clean = []
    for ln in lines:
        if not ln["text"].strip():
            breaks_before.add(len(clean))
            continue
        clean.append(ln)
    lines = clean

    if not lines:
        out.append("[Instrumental]")
        out.append(compress(chord_seq(chords, 0, audio_dur)))
        return out

    prev_chord = None
    first = lines[0]["start"]
    intro = chord_seq(chords, 0, first - LEAD_IN)
    if intro:
        out += ["[Intro]", compress(intro), ""]

    for i, ln in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else audio_dur
        gap = nxt - ln["end"]
        instrumental = gap > INSTR_GAP
        t1 = (ln["end"] + 0.6) if instrumental else (nxt - LEAD_IN)
        if i in breaks_before and out and out[-1] != "":
            out.append("")
        if out and out[-1] == "":
            prev_chord = None  # bloque nuevo: escribir siempre el acorde inicial
        cl = build_line(ln, chords, ln["start"] - LEAD_IN, t1, prev_chord)
        out.append(cl)
        out.append(ln["text"])
        prev_chord = cl.split()[-1] if cl.split() else prev_chord
        if i + 1 < len(lines):
            if instrumental:
                seq = chord_seq(chords, ln["end"] + 0.6, nxt - LEAD_IN)
                out.append("")
                if seq:
                    out += ["[Instrumental]", compress(seq), ""]
            elif gap > PARA_GAP and out[-1] != "":
                out.append("")
    outro = chord_seq(chords, lines[-1]["end"] + 0.6, audio_dur)
    if outro:
        out += ["", "[Final]", compress(outro)]
    # colapsar líneas vacías repetidas
    res = []
    for l in out:
        if l == "" and res and res[-1] == "":
            continue
        res.append(l)
    return res


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", help="link de YouTube o ruta a un archivo de audio")
    ap.add_argument("-o", "--salida", default=".", help="carpeta donde crear la carpeta de la canción")
    ap.add_argument("--artista")
    ap.add_argument("--titulo")
    ap.add_argument("--letra-archivo", help="archivo .txt con la letra (o .lrc sincronizado)")
    ap.add_argument("--idioma", default=None, help="código de idioma para Whisper (es, en, pt...). Por defecto, auto")
    ap.add_argument("--modelo-whisper", default="small",
                    help="tiny/base/small/medium/large-v3. Más grande = mejor pero más lento (default: small)")
    ap.add_argument("--sin-whisper", action="store_true", help="no usar Whisper (solo tiempos de LRCLIB)")
    ap.add_argument("--cookies-navegador", metavar="NAVEGADOR[+LLAVERO][:PERFIL]",
                    help="usar las cookies de YouTube de este navegador, con la sintaxis de yt-dlp "
                         "(firefox, chrome, 'chrome:Profile 1', 'chrome+gnomekeyring:Profile 1'...) "
                         "si YouTube pide verificación anti-bot")
    ap.add_argument("--sin-lrclib", action="store_true", help="no buscar la letra en LRCLIB")
    args = ap.parse_args()

    tmp = Path(args.salida).resolve() / ".acordes-tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)

    # --- 1. audio ---
    if is_url(args.entrada):
        log("Descargando audio de YouTube…")
        info = download_youtube(args.entrada, tmp, args.cookies_navegador)
        artist_guess, title_guess = (info["artist"], info["track"]) if info["artist"] and info["track"] else \
            split_artist_title(info["raw_title"], info["uploader"])
        src_audio, source = info["audio"], info["source"]
    else:
        src_audio = Path(args.entrada).expanduser().resolve()
        if not src_audio.exists():
            sys.exit(f"No existe el archivo: {src_audio}")
        tags = probe_tags(src_audio)
        if tags["artist"] and tags["track"]:
            artist_guess, title_guess = tags["artist"], tags["track"]
        else:
            artist_guess, title_guess = split_artist_title(src_audio.stem)
        source = src_audio.name
    artist = args.artista or artist_guess
    title = args.titulo or title_guess

    folder = Path(args.salida).resolve() / slugify(f"{artist} - {title}" if artist else title)
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / "audio.wav"
    to_wav(src_audio, wav)
    shutil.rmtree(tmp, ignore_errors=True)
    dur = probe_tags(wav)["duration"] or 0.0
    log(f"Canción: {artist} - {title} ({dur:.0f} s)")

    # --- 2. letra ---
    lyrics = None
    if args.letra_archivo:
        txt = Path(args.letra_archivo).read_text(encoding="utf-8")
        lines = parse_lrc(txt) if LRC_LINE.search(txt.splitlines()[0] if txt else "") else parse_plain(txt)
        lyrics = {"lines": lines, "source": "archivo", "match": args.letra_archivo}
    elif not args.sin_lrclib:
        log("Buscando letra en LRCLIB…")
        lyrics = fetch_lrclib(artist, title, dur)
        if lyrics:
            log(f"Letra encontrada: {lyrics['match']} [{lyrics['source']}]")
            # Los nombres de LRCLIB suelen venir bien escritos (tildes, mayúsculas).
            if lyrics.get("title") and not args.titulo and norm(lyrics["title"]) == norm(title):
                title = lyrics["title"]
            if lyrics.get("artist") and not args.artista and norm(lyrics["artist"]) == norm(artist):
                artist = lyrics["artist"]
        else:
            log("No se encontró la letra en LRCLIB")

    # --- 3. whisper ---
    wh = None
    if not args.sin_whisper:
        log("Transcribiendo con Whisper para obtener tiempos por palabra…")
        prompt, lang = None, args.idioma
        if lyrics:
            all_text = " ".join(l["text"] for l in lyrics["lines"] if l["text"])
            prompt = all_text[:600]
            lang = lang or guess_language(all_text)
        try:
            wh = run_whisper(wav, args.modelo_whisper, lang, prompt)
        except Exception as e:
            log(str(e))
    if lyrics is None:
        if not wh or not wh["words"]:
            log("Sin letra disponible: la hoja tendrá solo acordes")
            lyrics = {"lines": [], "source": "ninguna", "match": ""}
        else:
            lyrics = {"lines": lines_from_whisper(wh["segments"]), "source": "whisper (transcripción)",
                      "match": ""}

    lines = align(lyrics["lines"], wh["words"] if wh else [], dur) if lyrics["lines"] else []

    # --- 4. acordes ---
    log("Detectando pulso…")
    beats, bpm = detect_beats(wav)
    log("Detectando acordes…")
    raw_chords = detect_chords(wav)
    key = chordlib.estimate_key((c["chord"], c["end"] - c["start"]) for c in raw_chords if c["chord"] != "N")
    chords = clean_chords([dict(c) for c in raw_chords], beats, key)
    log(f"Tono estimado: {key}; tempo ≈ {bpm:.0f} BPM" if bpm else f"Tono estimado: {key}")

    # --- 5. hoja ---
    sheet = build_sheet({}, lines, chords, dur)
    header = [
        f"# Título: {title}",
        f"# Artista: {artist}",
        f"# Tono: {key}",
    ]
    if bpm:
        header.append(f"# Tempo: {bpm:.0f} BPM")
    header += [
        f"// Fuente: {source}",
        f"// Letra: {lyrics['source']} {('(' + lyrics['match'] + ')') if lyrics['match'] else ''}".rstrip(),
        "// Acordes: detección automática (madmom). Revisar antes de imprimir.",
        "",
    ]
    txt_path = folder / "cancion.txt"
    txt_path.write_text("\n".join(header + sheet) + "\n", encoding="utf-8")

    data = {
        "titulo": title, "artista": artist, "fuente": source, "duracion": dur,
        "tono": key, "bpm": bpm, "letra_fuente": lyrics["source"], "letra_match": lyrics["match"],
        "acordes": chords, "acordes_crudos": raw_chords, "pulsos": beats,
        "lineas": [{"texto": l["text"], "inicio": round(l["start"], 2), "fin": round(l.get("end") or 0, 2),
                    "palabras": [{"t": w["text"], "inicio": round(w["start"], 2)} for w in l["words"]]}
                   for l in lines],
    }
    (folder / "datos.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"carpeta": str(folder), "txt": str(txt_path), "tono": key, "bpm": bpm and round(bpm),
                      "letra": lyrics["source"], "lineas": len(lines), "acordes": len(chords)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
