# AgentFactory — color system

Dark base + violet→magenta accent. The Prometheus red is ONLY brand/logo (it
isn't used as a UI color, to avoid clashing with the "danger" state).
Each color has a single job.

## Tokens

| token       | hex                 | use                                   | COLOR_* in main_ui.py |
|-------------|---------------------|---------------------------------------|-----------------------|
| bg          | `#0C0E13`           | app background                        | COLOR_FONDO           |
| surface     | `#12151C`           | cards, transcript                     | COLOR_PANEL           |
| panel       | `#1A1E28`           | panels, neutral chips                 |                       |
| border      | `#262B38`           | borders                               |                       |
| muted       | `#6B7385`           | secondary text                        | COLOR_TENUE           |
| text        | `#E7EAF1`           | primary text                          | COLOR_TEXTO           |
| accent      | `#7C4DFF`           | interactive / focus                   | COLOR_USUARIO         |
| accent-2    | `#C63BFF`           | emphasis, hover                       |                       |
| gradient    | `#6D4BFF → #C63BFF` | hero, logo gear, CTA                  |                       |
| fire        | `#F5392B`           | brand / logo (not UI)                 |                       |
| fire-deep   | `#C21C17`           | base of the flame                     |                       |
| success     | `#37D39A`           | ran ok                                | COLOR_AGENTE          |
| warning     | `#F5B14C`           | attention                             |                       |
| danger      | `#FF5470`           | error / destructive                   | COLOR_ERROR           |
| info        | `#46B7E6`           | in progress                           |                       |

## Brand

- Logo/icon: `agentfactory-icon.png` (transparent) and `-dark.png`, `AppIcon.icns`.
- Original Prometheus flame: `prometeo-llama.png` / `-transparente.png`.
- Startup ASCII generated from the icon: `core/bienvenida.py` (`LOGO`).
