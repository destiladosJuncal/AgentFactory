"""Web UI (pywebview) — capa de ventana alternativa a la de Tkinter.

Migración por etapas: el `core/` (agentes, proxy, tareas, enjambre, seguridad) NO
se toca; esto es solo la capa de presentación. Se lanza con `main_web.py` y
convive con la UI vieja (`main_ui.py`) hasta que esté completa.

Etapa 1: vista de chat (lista de conversaciones, abrir, render HTML con tablas,
enviar con streaming). Próximas etapas: ▶ Ejecutar con confirmación de seguridad,
y el resto de las pestañas (tareas, agentes, config, proxy).
"""
