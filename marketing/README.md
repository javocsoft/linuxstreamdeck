# LinuxStreamDeck marketing gallery

These images are reproducible marketing captures built from the current
application and its real rendering paths. They do not use generated user
interfaces, borrowed hardware mockups or features that LinuxStreamDeck does not
provide.

- `01-main-window.png` uses the current canonical application screenshot.
- `02-obs-and-live-feedback.png` shows supported OBS actions and representative
  live feedback produced by the configured-key renderer.
- `03-integrations-and-audio.png` shows real Twitch, Home Assistant, Key Light,
  web, desktop, mixer and soundboard capabilities.
- `04-built-in-games.png` renders deterministic gameplay states through all
  eight game engines and their normal render dispatcher.
- `05-animated-screensavers.png` renders eight of the eleven installed animated
  screen savers through the full-deck screen-saver renderer.
- `06-adaptive-device-layouts.png` uses configured-key rendering for the Mini,
  Neo, MK.2, XL and Stream Deck + geometries, plus the real Stream Deck + LCD
  strip renderer. The MK.2 is tested on physical hardware; the other layouts
  are verified in simulation.

The dynamic numbers are representative values that the application can display,
not measurements captured from the developer's current computer. They are
included to demonstrate actual live-feedback states without exposing private
system or streaming data.

Regenerate the complete gallery from the project root with an isolated
configuration directory:

```bash
LSD_CONFIG_DIR="$(mktemp -d)" .venv/bin/python marketing/generate.py
```
