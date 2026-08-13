"""Where the text fonts are, on every distribution and sandbox this runs in.

Each renderer used to carry its own list of font paths, and every one of those
lists held only Debian and Arch locations. That is invisible until it is not:
Pillow answers a missing font by falling back to `ImageFont.load_default()`, a
10 px bitmap, without raising or logging anything. On Fedora, on openSUSE and
inside a Flatpak, every key label, the startup title, the screen saver and the
printable layout sheet therefore rendered unreadably small and nothing said
why.

The paths below were checked rather than guessed:

- Debian, Ubuntu and Pop!_OS keep DejaVu under `truetype/dejavu/`.
- Fedora keeps it in a per-package directory, `dejavu-sans-fonts/`.
- Arch and older Fedora/openSUSE use `TTF/` and `dejavu/`.
- The `org.freedesktop.Platform` runtime, which the Flatpak build sits on,
  ships it as `/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf` - a path none of
  the old lists had.

`BUNDLED_*` is the guarantee. A distribution can move its fonts or ship none at
all, and an AppImage may run somewhere with no fonts installed whatsoever, so
the package carries its own copy as the last entry. It is only reached when
nothing on the system matched, which keeps a machine's own DejaVu preferred.

Nothing here loads a font. Each renderer keeps its own loader so that the
`layout_engine=ImageFont.Layout.BASIC` argument stays visible at every call
site - see AGENTS.md section 5.1, and the invariant test that pins it.
"""

from __future__ import annotations

from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

BUNDLED_BOLD = str(_ASSETS / "DejaVuSans-Bold.ttf")
BUNDLED_REGULAR = str(_ASSETS / "DejaVuSans.ttf")

SANS_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",     # Debian, Ubuntu
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",   # Fedora
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",              # runtime, SUSE
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",                 # Arch
    "/usr/share/fonts/truetype/firasans/FiraSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/liberation-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    BUNDLED_BOLD,
)

SANS_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/firasans/FiraSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/liberation-fonts/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    BUNDLED_REGULAR,
)

# Fonts that may carry half-width katakana, for the Matrix Code rain. None of
# them is a dependency and none is bundled: 91 MB of CJK fonts is far too much
# to ship for one screen saver, and `_matrix_alphabet()` falls back to Latin
# and digits when the machine has none, so the style always renders.
CJK = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansJP-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",  # Fedora
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",         # Arch
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/truetype/vlgothic/VL-Gothic-Regular.ttf",
    "/usr/share/fonts/truetype/takao-gothic/TakaoPGothic.ttf",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/OTF/NotoSansCJK-Regular.ttc",
)


def first_present(candidates) -> str:
    """The first candidate that exists, or "" when none does.

    Callers still open the file themselves, because opening it is where the
    BASIC layout engine has to be named.
    """
    for path in candidates:
        if Path(path).exists():
            return path
    return ""
