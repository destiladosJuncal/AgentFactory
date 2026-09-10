# AgentFactory — sistema de color

Base oscura + acento violeta→magenta. El rojo de Prometeo es SOLO marca/logo
(no se usa como color de UI, para no chocar con el estado "peligro").
Cada color tiene un único trabajo.

## Tokens

| token       | hex                 | uso                                   | COLOR_* en main_ui.py |
|-------------|---------------------|---------------------------------------|-----------------------|
| bg          | `#0C0E13`           | fondo de la app                       | COLOR_FONDO           |
| surface     | `#12151C`           | tarjetas, transcripción               | COLOR_PANEL           |
| panel       | `#1A1E28`           | paneles, chips neutros                |                       |
| border      | `#262B38`           | bordes                                |                       |
| muted       | `#6B7385`           | texto secundario                      | COLOR_TENUE           |
| text        | `#E7EAF1`           | texto principal                       | COLOR_TEXTO           |
| accent      | `#7C4DFF`           | interactivo / foco                    | COLOR_USUARIO         |
| accent-2    | `#C63BFF`           | énfasis, hover                        |                       |
| gradiente   | `#6D4BFF → #C63BFF` | héroe, engranaje del logo, CTA        |                       |
| fire        | `#F5392B`           | marca / logo (no UI)                  |                       |
| fire-deep   | `#C21C17`           | base de la llama                      |                       |
| success     | `#37D39A`           | corrió ok                             | COLOR_AGENTE          |
| warning     | `#F5B14C`           | atención                              |                       |
| danger      | `#FF5470`           | error / destructivo                   | COLOR_ERROR           |
| info        | `#46B7E6`           | en progreso                           |                       |

## Marca

- Logo/ícono: `agentfactory-icon.png` (transparente) y `-dark.png`, `AppIcon.icns`.
- Llama original de Prometeo: `prometeo-llama.png` / `-transparente.png`.
- ASCII de arranque generado desde el ícono: `core/bienvenida.py` (`LOGO`).
