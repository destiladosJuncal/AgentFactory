# Novedades

## v0.2 — Nueva interfaz web + multi-agente

Esta versión trae **una interfaz nueva** (y es la que abre por defecto) y la
primera versión del **enjambre de agentes**.

### 🖥️ Nueva interfaz (web, con pywebview)

La cara de AgentFactory ahora se renderiza en HTML real (usando el WebKit del
sistema — sin Chromium ni dependencias nativas pesadas). Es la que abre por
defecto; `AGENTE_UI=tk` vuelve a la interfaz vieja (Tkinter), y si el webview no
se puede instalar, cae a la vieja sin romper nada.

Qué mejora respecto de la anterior:

- **Tablas de verdad**: con bordes, columnas que envuelven el texto largo, y
  que se pueden **seleccionar y copiar enteras** (era el dolor principal).
- **Texto seleccionable y copiable** en todo, con scroll fluido.
- **Conversaciones**: lista completa, **renombrar** el título, y un botón
  **📂 Abrir carpeta** para ir a la carpeta de la conversación.
- **Ejecuciones a la vista**: lo que el agente corre (shell, biblioteca,
  paquetes) aparece en la conversación como bloques **🔧** desplegables.
- **▶ Ejecutar** en los bloques de código (con confirmación de seguridad si el
  código es sensible) y **Copiar**.
- **Streaming** token a token.
- Pestañas: **💬 Chat · 🤝 Agentes · 🕒 Tareas · 🦊 Captura · ⚙️ Config**.

### 🤝 Enjambre de agentes (multi-agente)

- Un **planner infiere el equipo**: a partir de una tarea decide cuántos agentes
  y qué rol tiene cada uno (no hay que definirlos a mano).
- **Sala de chat** donde los agentes colaboran y **vos participás**: cada uno
  habla con su nombre y color; con **`@Nombre`** le hablás a uno solo (los demás
  no reciben ese mensaje) y sin `@` va a todos.
- **⏹ Detener** frena el enjambre (cancelación cooperativa).
- Cada agente conserva su conversación; en la lista, el enjambre se **agrupa**
  bajo un nodo **➕** que se despliega en sub-conversaciones.

### 🦊 Captura

- **Abrir Firefox** arranca el proxy y lanza el Firefox de captura de una.
- Tamaño de la captura a la vista y botón para **vaciarla**.

### 🔒 Seguridad

- Las ejecuciones de código mantienen el **guard**: lo que borra/pisa archivos o
  lee credenciales **pide confirmación** antes de correr.

### Bajo el capó

- La UI web comparte **los mismos datos** que la de escritorio (conversaciones,
  tareas, API keys): podés usar cualquiera de las dos.
- El `core` (agentes, proxy, tareas, seguridad) no cambió; solo se agregó la
  capa de presentación nueva.
- Parte del código interno empezó a pasarse a inglés (migración en curso).
