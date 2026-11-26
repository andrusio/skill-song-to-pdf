"""Sugerencias de extensiones (7, maj7, 6, sus4, sus2, add9, m7) sobre los acordes
mayor/menor que detecta madmom.

madmom decide raíz y calidad (es lo que mejor hace). Aquí, para cada acorde, se
mide en un cromagrama cuánto suena cada nota extra respecto de las notas de la
tríada y del "ruido" (las demás notas). Solo se sugiere una extensión cuando la
nota es clara durante el acorde y se repite en la mayoría de las veces que ese
acorde aparece: así se filtran notas de melodía o de paso.

Las sugerencias no se aplican solas a la hoja: van a datos.json y a un
comentario en cancion.txt para que el agente las valide con criterio musical.
"""
from __future__ import annotations

from collections import defaultdict

import chordlib

# (sufijo, intervalo en semitonos desde la raíz, requiere tercera ausente)
EXT_MAJOR = [("7", 10, False), ("maj7", 11, False), ("6", 9, False),
             ("sus4", 5, True), ("sus2", 2, True), ("add9", 2, False)]
EXT_MINOR = [("m7", 10, False)]

FPS = 10


def compute_chroma(wav):
    """Cromagrama acústico (CLP). No se usa DeepChroma de madmom porque está
    entrenado para mostrar solo notas de tríadas mayores/menores y borra las 7as."""
    from madmom.audio.chroma import CLPChroma

    return CLPChroma(str(wav), fps=FPS)  # (frames, 12), índice 0 = C


def _pc_profile(chroma, start, end):
    """Perfil medio de las 12 clases de altura (índice 0 = C) en el centro del acorde."""
    import numpy as np

    a, b = int(start * FPS), int(end * FPS)
    trim = int((b - a) * 0.15)  # descartar bordes (transiciones)
    seg = chroma[a + trim: max(a + trim + 1, b - trim)]
    if len(seg) == 0:
        return None
    return np.asarray(seg).mean(axis=0)


def score_occurrence(prof, chord):
    """Puntuación 0..1+ de cada extensión candidata para un acorde concreto."""
    import numpy as np

    t = chordlib.triad_of(chord)
    if t is None:
        return {}
    root, minor = t[0], t[1] == "m"
    third = (root + (3 if minor else 4)) % 12
    fifth = (root + 7) % 12
    triad = [root, third, fifth]
    others = [p for p in range(12) if p not in triad]
    base = float(np.median(prof[others]))
    ref = float(np.mean(prof[[root, fifth]]))  # raíz y quinta: siempre presentes
    span = max(ref - base, 1e-6)
    third_lvl = (prof[third] - base) / span
    out = {}
    for suf, iv, needs_no_third in (EXT_MINOR if minor else EXT_MAJOR):
        lvl = (prof[(root + iv) % 12] - base) / span
        if needs_no_third:
            # sus: la tercera debe ser débil (los armónicos del bajo siempre dejan algo:
            # el 5º armónico de la raíz es su tercera mayor) y claramente más débil que la 4ª/2ª.
            lvl = lvl if (third_lvl < 0.45 and lvl > third_lvl + 0.1) else 0.0
        elif suf in ("add9",) and third_lvl < 0.5:
            lvl = 0.0
        out[suf] = float(lvl)
    return out


def suggest(chords, wav, min_dur=0.8, occ_thr=0.55, share_thr=0.6):
    """Devuelve (sugerencias_por_ocurrencia, resumen_por_acorde).

    resumen: {"G": {"sugerencia": "G7", "veces": 5, "de": 7, "fuerza": 0.82}, ...}
    """
    chroma = compute_chroma(wav)
    per_occ = []
    stats = defaultdict(lambda: {"n": 0, "hits": defaultdict(list)})
    for i, c in enumerate(chords):
        if c["chord"] == "N" or c["end"] - c["start"] < min_dur:
            continue
        prof = _pc_profile(chroma, c["start"], c["end"])
        if prof is None:
            continue
        scores = score_occurrence(prof, c["chord"])
        st = stats[c["chord"]]
        st["n"] += 1
        best = max(scores.items(), key=lambda kv: kv[1], default=(None, 0))
        if best[0] and best[1] >= occ_thr:
            st["hits"][best[0]].append(best[1])
            per_occ.append({"i": i, "inicio": round(c["start"], 2), "acorde": c["chord"],
                            "sugerencia": _apply(c["chord"], best[0]), "fuerza": round(best[1], 2)})
    summary = {}
    for chord, st in stats.items():
        if not st["hits"]:
            continue
        suf, vals = max(st["hits"].items(), key=lambda kv: len(kv[1]))
        share = len(vals) / st["n"]
        if share >= share_thr and len(vals) >= 2:
            summary[chord] = {"sugerencia": _apply(chord, suf), "veces": len(vals), "de": st["n"],
                              "fuerza": round(sum(vals) / len(vals), 2)}
    return per_occ, summary


def _apply(chord, suf):
    root = chord[:-1] if chord.endswith("m") else chord
    return root + suf  # m7 ya incluye la "m"
