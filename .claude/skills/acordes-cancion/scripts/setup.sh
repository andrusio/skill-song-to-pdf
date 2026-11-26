#!/usr/bin/env bash
# Comprueba requisitos y precalienta los entornos (dependencias + modelo Whisper)
# para que la primera canción no tarde de más.
#   bash setup.sh            # comprobar e instalar dependencias Python
#   bash setup.sh --modelo medium   # además precarga ese modelo de Whisper
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="small"
[ "${1:-}" = "--modelo" ] && MODEL="${2:-small}"

ok=1
check() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "  ✔ $1"
  else
    echo "  ✘ falta $1 — $2"; ok=0
  fi
}
echo "Requisitos del sistema:"
check uv     "curl -LsSf https://astral.sh/uv/install.sh | sh"
check ffmpeg "sudo apt install ffmpeg   (macOS: brew install ffmpeg)"
check git    "sudo apt install git"
if command -v deno >/dev/null 2>&1 || command -v node >/dev/null 2>&1 || command -v bun >/dev/null 2>&1; then
  echo "  ✔ intérprete de JavaScript (para YouTube)"
else
  echo "  ✘ falta un intérprete de JavaScript (YouTube lo exige) — sudo apt install nodejs"; ok=0
fi
if command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || command -v clang >/dev/null 2>&1; then
  echo "  ✔ compilador C"
else
  echo "  ✘ falta un compilador C (para compilar madmom) — sudo apt install build-essential   (macOS: xcode-select --install)"; ok=0
fi
[ $ok = 1 ] || { echo "Instala lo que falta y vuelve a ejecutar."; exit 1; }

echo "Instalando dependencias Python (solo la primera vez, puede tardar)…"
uv run "$DIR/acordes.py" --help >/dev/null || exit 1
uv run "$DIR/render_pdf.py" --help >/dev/null || exit 1

echo "Descargando modelo Whisper '$MODEL'…"
uv run --with faster-whisper --python 3.12 python -c \
  "from faster_whisper import WhisperModel; WhisperModel('$MODEL', device='cpu', compute_type='int8')" \
  || echo "  (no se pudo precargar el modelo; se descargará en el primer uso)"
CFG="$HOME/.config/acordes-cancion/navegador"
if [ -s "$CFG" ]; then
  echo "Navegador para cookies de YouTube: $(cat "$CFG")"
else
  echo "Falta elegir el navegador para las cookies de YouTube (se pide en el primer uso,"
  echo "o guardalo ahora:  mkdir -p ~/.config/acordes-cancion && echo 'chrome+gnomekeyring:Profile 1' > $CFG)"
fi
echo "Listo."
