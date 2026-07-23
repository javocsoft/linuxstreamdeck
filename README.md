# LinuxStreamDeck

Software para **Elgato Stream Deck** en Linux con integración **OBS Studio completa**
(obs-websocket v5) y feedback de estado en tiempo real en las teclas.

## Características

- **Deck virtual en la UI**: la rejilla de la ventana refleja el deck físico y permite
  configurar y probar acciones sin tener el aparato conectado.
- **Organización cómoda**: mueve teclas con **arrastrar y soltar** (intercambia
  posiciones) y **copia/pega** cualquier tecla para duplicarla (clic derecho →
  Copiar/Pegar, o `Ctrl+C`/`Ctrl+V`; `Supr` la limpia). Funciona con cualquier tipo de tecla.
- **Tres tipos de tecla**:
  - *Acción simple* — una acción, con feedback de estado en la tecla.
  - *Acciones múltiples* — lista ordenada de acciones que se ejecutan en secuencia
    al pulsar (con espera opcional entre pasos).
  - *Conmutable (ON/OFF)* — dos listas de acciones; cada pulsación alterna el estado
    y ejecuta la lista correspondiente, con apariencia propia por estado.
- **Integración OBS a fondo**, más completa que la mayoría de alternativas en Linux:
  - Escenas: cambiar programa/previsualización, modo estudio, transiciones (tipo y duración)
  - Grabación: iniciar/parar/pausar · Directo: iniciar/parar · Cámara virtual
  - Replay buffer: activar y guardar · Capturas de fuente a PNG
  - Audio: silenciar (con feedback), subir/bajar volumen, fijar volumen en dB
  - Fuentes: mostrar/ocultar por escena · Filtros: activar/desactivar
  - Media: reproducir/pausar/reiniciar/detener/siguiente/anterior
  - Colecciones de escenas y perfiles · Hotkeys internos de OBS
  - **Petición cruda**: cualquier petición del protocolo obs-websocket → cobertura 100%
- **Feedback en tiempo real**: escena activa iluminada, tecla de grabación en rojo,
  micrófono silenciado marcado… vía eventos de obs-websocket.
- **Biblioteca de iconos integrada** (~7.400 iconos de Material Design Icons,
  categorizados y buscables): cada acción trae ya un icono por defecto, puedes
  elegir otro de la biblioteca, o usar tu propia imagen. Sin subir nada a mano.
- Acciones de sistema (ejecutar comando, abrir URL) y navegación entre páginas.
- Reconexión automática con OBS y hotplug del dispositivo.

## Requisitos

- Pop!_OS / Ubuntu 24.04 o similar, Python ≥ 3.10
- OBS Studio 28+ con el servidor WebSocket activado
  (*Herramientas → Ajustes del servidor WebSocket*)

## Instalación

Forma rápida con los scripts incluidos:

```bash
# 1. Preparar el proyecto: crea el entorno virtual, instala dependencias y
#    comprueba que compila. Con --apt instala también los paquetes del sistema.
./build.sh --apt

# 2. Permisos USB para el Stream Deck (una sola vez)
sudo ./install-udev.sh
```

<details>
<summary>Pasos manuales (equivalente, sin usar build.sh)</summary>

```bash
# Dependencias del sistema
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 libhidapi-libusb0 python3-gi python3-gi-cairo

# Entorno Python (con acceso a GTK/PyGObject del sistema)
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .

# Permisos USB
sudo ./install-udev.sh
```
</details>

## Uso

```bash
./run.sh                 # arranca la app
LSD_DEBUG=1 ./run.sh     # con log de depuración
```

(equivale a `.venv/bin/linuxstreamdeck`)

1. Pulsa el botón de red de la cabecera y configura host/puerto/contraseña de obs-websocket.
2. Haz clic en una tecla de la rejilla, elige el **tipo de tecla** (simple, múltiple o
   conmutable), la categoría y la acción, rellena los parámetros (los desplegables se
   rellenan en vivo desde OBS) y pulsa **Guardar**.
3. En **Icono**, elige uno de la biblioteca integrada, usa tu propia imagen, o deja el
   que trae la acción por defecto. Añade una **Etiqueta** solo si quieres texto en la tecla.
4. **Probar** ejecuta la acción sin necesidad del deck físico.
5. **Reordena** las teclas arrastrándolas de una posición a otra, y **duplica** una
   tecla con clic derecho → Copiar y luego Pegar sobre otra (o `Ctrl+C`/`Ctrl+V`).

> La app es de **instancia única**: cierra cualquier ventana anterior antes de abrir otra.

La configuración se guarda en `~/.config/linuxstreamdeck/config.json`.

## Estructura

```
build.sh · run.sh · install-udev.sh    # preparar / lanzar / permisos USB
linuxstreamdeck/
├── core/          # bus de eventos, config, registro de acciones, controlador, iconos
├── device/        # Stream Deck físico (hidapi) y renderizado de teclas (Pillow)
├── obs/           # cliente obs-websocket v5 + catálogo completo de acciones OBS
├── ui/            # GTK4/Libadwaita: ventana, editor, selector de iconos, ajustes OBS
└── assets/icons/  # biblioteca de iconos (fuente Material Design Icons + índice)
data/udev/         # regla udev para el acceso al dispositivo
```

## Licencia

GPL-3.0-or-later — © JavocSoft
