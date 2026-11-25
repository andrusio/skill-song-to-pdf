"""Utilidades de acordes sin dependencias externas: parseo, transposición,
detección de tonalidad y deletreo (sostenidos/bemoles) según la tonalidad."""
from __future__ import annotations

import re

SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
NOTE_TO_PC = {n: i for i, n in enumerate(SHARP)}
NOTE_TO_PC.update({n: i for i, n in enumerate(FLAT)})
NOTE_TO_PC.update({"Cb": 11, "Fb": 4, "E#": 5, "B#": 0})

# Tonalidades que se escriben con bemoles (mayores y menores relativas).
FLAT_MAJOR_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb"}
FLAT_MINOR_KEYS = {"D", "G", "C", "F", "Bb", "Eb"}

# Acorde: raíz + calidad + bajo opcional. Admite Am, F#m7, Bbmaj7, Dsus4, C/E, A7(b9)...
CHORD_RE = re.compile(
    r"^(?P<root>[A-G](?:#|b)?)"
    r"(?P<qual>(?:maj|min|m|M|dim|aug|sus|add|º|°|\+|-)?[0-9]*(?:\([^)]*\))?"
    r"(?:(?:maj|min|m|M|dim|aug|sus|add|b|#|\+|-)?[0-9]+(?:\([^)]*\))?)*)"
    r"(?:/(?P<bass>[A-G](?:#|b)?))?$"
)
# Tokens que pueden aparecer en una línea de acordes sin ser acordes.
NON_CHORD_TOKENS = re.compile(r"^(\||\|\||-+|/|%|\.+|N\.?C\.?|\(?x\d+\)?|\(|\)|:\|\|?|\|\|?:)$", re.I)


def is_chord(token: str) -> bool:
    return bool(CHORD_RE.match(token))


def is_chord_line(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    n_chords = sum(1 for t in tokens if is_chord(t))
    return n_chords > 0 and all(is_chord(t) or NON_CHORD_TOKENS.match(t) for t in tokens)


def parse(chord: str):
    m = CHORD_RE.match(chord)
    if not m:
        return None
    return m.group("root"), m.group("qual") or "", m.group("bass")


def spell(pc: int, flats: bool) -> str:
    return (FLAT if flats else SHARP)[pc % 12]


def key_uses_flats(key: str) -> bool:
    """key como 'Bb' o 'Gm'."""
    if key.endswith("m"):
        return key[:-1] in FLAT_MINOR_KEYS
    return key in FLAT_MAJOR_KEYS


def transpose_chord(chord: str, semitones: int, flats: bool | None = None) -> str:
    p = parse(chord)
    if not p:
        return chord
    root, qual, bass = p
    if flats is None:
        flats = "b" in root
    out = spell(NOTE_TO_PC[root] + semitones, flats) + qual
    if bass:
        out += "/" + spell(NOTE_TO_PC[bass] + semitones, flats)
    return out


def transpose_key(key: str, semitones: int) -> str:
    minor = key.endswith("m")
    root = key[:-1] if minor else key
    pc = (NOTE_TO_PC[root] + semitones) % 12
    # Elegir el deletreo más habitual para la nueva tonalidad.
    for flats in (False, True):
        cand = spell(pc, flats) + ("m" if minor else "")
        if key_uses_flats(cand) == flats:
            return cand
    return spell(pc, False) + ("m" if minor else "")


def transpose_line(line: str, semitones: int, flats: bool) -> str:
    """Transpone una línea de acordes intentando conservar las columnas."""
    out = []
    pending_shift = 0  # caracteres que hay que recuperar si un acorde creció
    for m in re.finditer(r"\S+|\s+", line):
        tok = m.group(0)
        if tok.isspace():
            n = len(tok) - pending_shift
            if n < 1:
                pending_shift = 1 - n
                n = 1
            else:
                pending_shift = 0
            out.append(" " * n)
        elif is_chord(tok):
            new = transpose_chord(tok, semitones, flats)
            pending_shift += len(new) - len(tok)
            if pending_shift < 0:  # el acorde se acortó: rellenar para no correr la columna
                new += " " * (-pending_shift)
                pending_shift = 0
            out.append(new)
        else:
            out.append(tok)
    return "".join(out).rstrip()


# --- Tonalidad -------------------------------------------------------------

MAJOR_DIATONIC = [(0, ""), (2, "m"), (4, "m"), (5, ""), (7, ""), (9, "m")]
# Menor natural + V mayor (armónica), muy habitual.
MINOR_DIATONIC = [(0, "m"), (3, ""), (5, "m"), (7, "m"), (7, ""), (8, ""), (10, "")]


def triad_of(chord: str):
    """Reduce un acorde a (pc, 'm' | '')."""
    p = parse(chord)
    if not p:
        return None
    root, qual, _ = p
    minor = qual.startswith("m") and not qual.startswith("maj")
    return NOTE_TO_PC[root], ("m" if minor else "")


def estimate_key(chords_with_dur) -> str:
    """chords_with_dur: iterable de (acorde, duración). Devuelve p.ej. 'E' o 'C#m'."""
    weights = {}
    first = last = None
    for ch, d in chords_with_dur:
        t = triad_of(ch)
        if t is None:
            continue
        weights[t] = weights.get(t, 0.0) + d
        first = first or t
        last = t
    best, best_score = "C", -1.0
    for tonic in range(12):
        for minor, pattern in ((False, MAJOR_DIATONIC), (True, MINOR_DIATONIC)):
            tonic_triad = (tonic, "m" if minor else "")
            score = sum(weights.get(((tonic + iv) % 12, q), 0.0) for iv, q in pattern)
            score += 0.5 * weights.get(tonic_triad, 0.0)  # la tónica suele dominar
            if last == tonic_triad:
                score *= 1.05
            if first == tonic_triad:
                score *= 1.03
            if score > best_score:
                best_score = score
                name_sharp = spell(tonic, False) + ("m" if minor else "")
                name_flat = spell(tonic, True) + ("m" if minor else "")
                best = name_flat if key_uses_flats(name_flat) else name_sharp
    return best
