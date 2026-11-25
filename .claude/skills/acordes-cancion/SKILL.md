---
name: acordes-cancion
description: Saca la letra con acordes de una canción (formato cancionero tipo lacuerda.net / ultimate-guitar, acordes encima de la sílaba) a partir de un link de YouTube o de un archivo de audio (mp3, wav, m4a, ogg, flac, opus, nota de voz…) y entrega un PDF listo para tocar. Úsalo siempre que el usuario mande un link de YouTube o un audio y quiera los acordes, el cifrado, la letra con acordes, "sacar" o "transcribir" una canción, o una hoja para tocarla en guitarra, piano o ukelele, aunque no diga "PDF" ni nombre este skill. También para transponer o poner cejilla a una hoja ya generada.
---

# Letra con acordes desde YouTube o audio

El trabajo es mitad máquina, mitad oído musical. Los scripts hacen lo pesado
(descargar, buscar la letra, situar cada palabra en el tiempo, detectar acordes
y pulso, maquetar el PDF). Tu parte es la de un músico que revisa la hoja antes
de entregarla: la detección automática acierta la mayoría de los acordes en
pop/rock, pero mete errores sueltos que un músico ve enseguida.

Todos los scripts están en `scripts/` dentro de la carpeta de este skill
(abajo, `$SKILL` es esa carpeta; normalmente `~/.hermes/skills/acordes-cancion`
o `.claude/skills/acordes-cancion`). Se ejecutan con `uv run`, que instala solo sus
dependencias la primera vez (unos minutos y ~1 GB; después es inmediato).
Requisitos del sistema: `uv`, `ffmpeg`, `git` y un compilador C. Si algo falta,
`bash $SKILL/scripts/setup.sh` lo comprueba y dice qué instalar.

## 1. Generar el borrador

```bash
uv run $SKILL/scripts/acordes.py "<link de YouTube o ruta al audio>" -o <carpeta de salida>
```

Con un audio enviado por el usuario, pasa la ruta donde quedó guardado. Si el
nombre del archivo no dice artista y título (p. ej. `audio_2024-05-01.ogg`),
pregúntalos o dedúcelos de la conversación y pásalos con `--artista` y
`--titulo`: con ellos se busca la letra correcta.

Opciones útiles:

| Opción | Cuándo |
|---|---|
| `--artista A --titulo T` | El título de YouTube es confuso, o el audio no tiene etiquetas. |
| `--letra-archivo letra.txt` | El usuario te da la letra (texto o `.lrc`), o es una canción propia/inédita. |
| `--idioma es` | Whisper detecta mal el idioma (se deduce solo si hay letra). |
| `--modelo-whisper medium` | No hay letra en LRCLIB y la transcripción salió mala. Más lento. |
| `--sin-lrclib` | La letra de LRCLIB es de otra versión (en vivo, remaster) y queda desfasada. |
| `--cookies-navegador NAVEGADOR[+LLAVERO][:PERFIL]` | YouTube responde "Sign in to confirm you're not a bot". Usa las cookies de sesión del navegador del usuario: **pedile permiso antes**, y preguntale qué navegador y perfil usa. Ejemplos: `firefox`, `chrome`, `"chrome:Profile 1"`, `"chrome+gnomekeyring:Profile 1"`. |

Tarda 1–3 min por canción en CPU. Imprime un JSON final con la carpeta creada,
que contiene:

- `cancion.txt` — la hoja editable (lo que vas a revisar).
- `datos.json` — acordes con tiempos (`acordes`), palabras con tiempos (`lineas`), tono y BPM. Útil para dudas concretas.
- `audio.wav` — el audio.

**De dónde sale la letra** (lo indica la línea `// Letra:` del txt):
`lrclib` (letra publicada y sincronizada: lo mejor), `lrclib (sin tiempos)`,
`archivo`, o `whisper (transcripción)` — esta última es una transcripción
automática del canto y puede traer palabras mal oídas; avisa al usuario.

## 2. Revisar la hoja como músico

Lee `cancion.txt` entero y corrígelo editando el archivo. Lo que hay que mirar,
por orden de impacto:

1. **Tonalidad.** El `# Tono:` es una estimación. Si la progresión gira en torno
   a la relativa menor (p. ej. empieza y resuelve en Bm aunque diga D), cámbialo.
2. **Acordes fuera de lugar.** Un acorde fuera del tono que aparece una sola vez
   (un `B` mayor suelto en una canción en D, un `E` aislado) casi siempre es un
   error de detección. Ojo: un V mayor en tono menor, un IV/V prestado o un
   acorde de paso repetido en todos los estribillos son legítimos. El criterio
   es **la repetición**: las partes que se repiten (todas las estrofas, todos
   los estribillos) normalmente llevan la misma progresión. Compara las
   repeticiones y quédate con la versión mayoritaria.
3. **Secciones.** Pon etiquetas como `[Estrofa]`, `[Pre-estribillo]`,
   `[Estribillo]`, `[Puente]`, `[Solo]`. El estribillo es el bloque de letra que
   se repite igual. Renombra los `[Instrumental]` del medio si está claro que son
   un solo o un interludio.
4. **Instrumentales.** El script compacta las progresiones repetidas
   (`Bm   G   D   A   (x6)`) y, si hubo variaciones, deja debajo una línea
   `// secuencia detectada completa: …`. Decide si la variación era real o
   ruido, ajusta el número de repeticiones y **borra los comentarios `//`** del
   cuerpo (no se imprimen, pero limpia el archivo).
5. **Zonas sin acorde.** Si la detección deja huecos (intro solo de batería,
   un corte) y el audio realmente no tiene armonía ahí, escribe `N.C.`; si
   sospechas que hay acordes que no se detectaron, dilo en el resumen en vez de
   inventarlos.
6. **Acordes encima de la línea.** El acorde de una línea se escribe al
   principio solo si cambia respecto al final de la anterior; un acorde de
   paso que cierra un ciclo va al final de la línea donde suena. Cada acorde cae sobre la palabra donde
   cambia. Si al corregir un acorde cambia su longitud, mantén las columnas
   (el formato es monoespaciado: los espacios importan).

Lo que **no** hay que hacer:
- No inventes extensiones (7, maj7, sus4, add9…): el detector solo distingue
  mayor/menor. Añádelas solo si el usuario las pide o te da la armonía.
- No reescribas la letra salvo errores evidentes de transcripción de Whisper.
- No copies la letra en el chat: entrégala dentro del PDF.

Si la canción es muy distinta de lo habitual (jazz, armonía modal, solo
instrumental sin letra), dilo en el resumen: la detección es menos fiable ahí.

### Formato de `cancion.txt`

```
# Título: Nombre de la canción        <- cabecera: líneas "# Clave: valor" al principio
# Artista: Artista
# Tono: Bm
# Tempo: 125 BPM
// comentario: no se imprime

[Intro]
Bm   G   D   A   (x2)

[Estrofa]
Bm          G      D
primera línea de la letra
A                  Bm
segunda línea
```

Las líneas cuyos tokens son todos acordes (`Am`, `F#m`, `Bb/D`, `|`, `(x2)`,
`N.C.`) se pintan como acordes; `[...]` como sección; el resto como letra.
Las líneas en blanco separan bloques (un bloque no se parte entre columnas).

## 3. Generar el PDF

```bash
uv run $SKILL/scripts/render_pdf.py <carpeta>/cancion.txt -o "<carpeta>/<Artista> - <Título>.pdf"
```

- `--transponer N` — sube/baja N semitonos ("pásala a G", "un tono más abajo" = `-2`).
- `--capo N` — imprime las formas de acordes con cejilla en el traste N, manteniendo el tono real (aparece en la cabecera).
- `--columnas 1|2` y `--tamano PT` — por defecto se ajusta solo (dos columnas si cabe en menos páginas).

El `.txt` siempre guarda los acordes en el tono real; transposición y cejilla
se aplican solo al imprimir, así se pueden sacar varias versiones del mismo txt.

Si el usuario pide un tono cómodo para guitarra, sugiere una cejilla que deje
formas abiertas (p. ej. tono Bb → `--capo 1` con formas de A, o `--capo 3`
con formas de G).

## 4. Entregar

Envía el PDF (y menciona dónde quedó el `.txt` por si quiere retocarlo). Acompáñalo
de un resumen breve: tono, tempo, de dónde salió la letra, y lo que
corregiste o dudas que quedan (p. ej. "en el puente no tengo claro si es G o Em").

## Problemas comunes

- **"Sign in to confirm you're not a bot"**: explícale al usuario que YouTube
  bloqueó la descarga y ofrécele dos salidas: que te deje usar las cookies de su
  navegador (`--cookies-navegador firefox`/`chrome`/…; solo con su permiso,
  porque accede a su sesión) o que te mande el audio.
  En Linux, Chrome/Chromium/Brave cifran las cookies con el llavero del escritorio:
  si con `chrome` solo no alcanza, indica el llavero y el perfil
  (`"chrome+gnomekeyring:Profile 1"`; en KDE, `+kwallet`). Los perfiles están en
  `~/.config/google-chrome/` (carpetas `Default`, `Profile 1`…); el que tiene
  YouTube abierto suele ser el que más se usa. El script ya conecta con el
  llavero de la sesión aunque el agente corra fuera del escritorio.
- **Whisper avisa que falta `libcublas`/CUDA**: es normal sin GPU NVIDIA con CUDA;
  sigue en CPU, solo tarda algo más.
- **YouTube cambió algo y yt-dlp falla**: `uv run --upgrade-package yt-dlp $SKILL/scripts/acordes.py …`.
- **Sin letra en LRCLIB**: se transcribe con Whisper. Si la transcripción es pobre,
  pide la letra al usuario y usa `--letra-archivo`.
- **La letra va desfasada de los acordes** (acordes sobre palabras equivocadas
  en toda la canción): suele ser que LRCLIB trajo otra versión (en vivo,
  remaster). Mira `// Letra:` y la duración; prueba `--sin-lrclib` o pasa la
  letra a mano.
- **El primer uso tarda mucho**: es la instalación de dependencias y la
  descarga del modelo de Whisper; las siguientes son rápidas.
