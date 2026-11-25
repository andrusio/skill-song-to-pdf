# acordes-canciones

Skill para agentes (Hermes, Claude Code y cualquier agente compatible con el
formato `SKILL.md`): le pasás un link de YouTube o un archivo de audio y te
devuelve un PDF con la letra y los acordes encima, estilo lacuerda.net /
ultimate-guitar.

El skill está en [`.claude/skills/acordes-cancion/`](.claude/skills/acordes-cancion/SKILL.md).

## Cómo funciona

1. **Audio**: `yt-dlp` baja el audio de YouTube (o se usa el archivo que mandes).
2. **Letra**: se busca la letra sincronizada en [LRCLIB](https://lrclib.net); si no está, se transcribe con Whisper.
3. **Tiempos por palabra**: Whisper ubica cada palabra en el tiempo y se cruza con la letra.
4. **Acordes y pulso**: [madmom](https://github.com/CPJKU/madmom) detecta acordes (mayor/menor), tempo y tono.
5. **Hoja**: cada acorde se escribe encima de la palabra donde cambia → `cancion.txt` editable.
6. **Revisión**: el agente corrige como músico (tono, acordes sueltos mal detectados, secciones).
7. **PDF**: `render_pdf.py`, con opción de transponer o poner cejilla.

## Instalación en la notebook (Hermes)

Requisitos: `uv`, `ffmpeg`, `git` y un compilador C.

```bash
sudo apt install ffmpeg git build-essential
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Copiar el skill a la carpeta de skills de Hermes y precalentar (instala
dependencias y baja el modelo de Whisper, ~1 GB la primera vez):

```bash
cp -r .claude/skills/acordes-cancion ~/.hermes/skills/
bash ~/.hermes/skills/acordes-cancion/scripts/setup.sh
```

Después, en Hermes: *"sacame los acordes de https://youtu.be/…"* o mandarle un audio.

## Uso manual (sin agente)

```bash
uv run .claude/skills/acordes-cancion/scripts/acordes.py "https://youtu.be/…" -o ~/Canciones
# revisar/editar ~/Canciones/<Artista - Título>/cancion.txt
uv run .claude/skills/acordes-cancion/scripts/render_pdf.py "~/Canciones/<Artista - Título>/cancion.txt" --capo 2
```

## Límites

- El detector solo distingue acordes mayores y menores (sin 7ª, sus, etc.).
- Acierta bien en pop/rock; en jazz o armonía compleja, menos.
- En CPU tarda 1–3 minutos por canción. Con GPU NVIDIA y CUDA instalado, Whisper la usa sola.
