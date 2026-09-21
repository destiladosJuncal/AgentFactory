# AgentFactory

Conversational desktop assistant (Python + Tkinter) that runs on
**Windows 10/11**, **macOS** (Apple Silicon and Intel) and **Linux** (Debian/RHEL
and derivatives) from the same code.

It's not just a chat with a model. The three things that set it apart:

- **It builds tools and keeps them.** The iterative mode writes code, runs it,
  scores it, and retries until it works. Whatever comes out well is published to
  a shared library and stays available for the next conversation.
- **It captures what you browse.** A built-in proxy (mitmproxy → SQLite) records
  the web traffic, so the agent can infer which site you're talking about and
  extract what you saw, without you having to copy and paste anything.
- **It wakes up on its own.** Scheduled tasks run with the app closed, via the
  system scheduler, and save their result with a history.

Talks to DeepSeek, Claude, Qwen and Gemini.

---

## Installation

**You don't need Python installed.** If the machine doesn't have a usable one,
the launcher downloads one (~30 MB) and drops it inside the project folder. It
installs nothing on the system, asks for no administrator privileges and doesn't
touch the registry: if you delete the folder, no trace is left.

```
git clone https://github.com/destiladosJuncal/AgentFactory.git
cd AgentFactory
```

And then, depending on the platform:

### Windows 10 / 11 (64-bit)

Double-click **`INICIAR.bat`**, or from a console:

```
INICIAR.bat
```

The first time it downloads Python, installs the dependencies and opens the app;
it takes a couple of minutes and needs internet. Subsequent launches are direct
and console-free.

If *"Windows protected your PC"* (SmartScreen) appears, it's because the file
isn't signed: *More info* → *Run anyway*. And if you downloaded a zip instead of
cloning, Windows may mark the `.bat` as downloaded: right-click → *Properties* →
**Unblock**.

### macOS (Apple Silicon or Intel)

Double-click **`INICIAR.command`**, or from the Terminal:

```
./INICIAR.command
```

If you cloned the repo, the execute bit is already set. If you downloaded a zip,
you need to set it once: `chmod +x INICIAR.command`.

The first time macOS may say it can't verify the developer (the script isn't
signed with an Apple Developer ID): right-click **`INICIAR.command`** →
**Open** → Open.

### Linux (Debian/Ubuntu, Fedora/RHEL and derivatives)

From a terminal:

```
./INICIAR.sh
```

A single build covers both families (they share bash and systemd). The first
time it downloads a Python with tkinter into the folder if there isn't a usable
one. Scheduled tasks use **systemd user timers** (`systemctl --user`, no sudo).
If tkinter won't open, the script tells you the missing X11 package
(`apt install libx11-6 …` / `dnf install libX11 …`). See `HANDOFF-LINUX.md`.

### Requirements

- Windows 10 version 1803 or later (it needs `tar.exe`, which ships with the
  system since then) / Windows 11, 64-bit. macOS with Apple Silicon or Intel. Or
  Linux (x86_64 / aarch64) with a desktop environment.
- Internet connection the first time.
- Firefox, **only** if you're going to use traffic capture.

### First launch

The app opens in **⚙️ Settings** because no API key is loaded yet. Enter the one
for the provider you use and hit **Test**: it makes a real call and tells you
what happened, instead of failing only when you try to chat.

---

## What it does

### Conversations

Each conversation is a folder with its own history, workspace and permissions.
The agent has **21 tools**: read and write files, run shell and Python, install
packages, query the proxy capture, plus the library and iterative-mode tools
described below.

The transcript renders as Markdown: tables, code with a run button, and images
shown inline. Below each message you get the tokens and the estimated cost of the
call.

### The shared library

The problem it solves: without it, every conversation starts from scratch and
the agent rebuilds the same things over and over.

When the agent builds something that works —an extractor, an API wrapper, a
converter— it **publishes** it to the library, which lives outside any project.
In the next conversation it can list the library, read a module to see which
functions it exposes, and run it, instead of writing it again.

The four tools: `listar_biblioteca`, `leer_modulo_biblioteca`,
`publicar_modulo_biblioteca`, `ejecutar_modulo_biblioteca`.

The flow the system prompt asks it to follow when you request an action: first
look at the library; if there's something useful, use it; if it doesn't exist,
build it with the iterative mode and publish it. That way the library grows with
each thing you do together.

### The iterative mode

It's the engine that **builds** tools, rather than just running them. The cycle
is: goal → generate code → run it → score it → diagnose what failed → retry.

The evaluator scores functionality (do the tests pass?), efficiency and quality,
and returns a diagnosis that feeds into the retry prompt. The goals and the
minimum score are configured in `config/objetivos.json`.

It can be used two ways:

- **From the conversation**, with the `iterar_codigo` tool: the agent kicks off
  an iterative run without you leaving the chat. It runs non-interactively (it
  can't stop to ask you halfway) and has a cap of 10 iterations. Deliberately,
  this tool is **not** offered to the inner generator, so an iterative run can't
  recursively trigger another.
- **As a separate program**, with `main_interactivo.py`, which *can* pause and
  ask you between iterations.

Projects are persistent: a run kicked off from the chat creates a normal project
you can keep iterating by hand afterwards.

### "Yes to everything"

It's narrower than it sounds, and it's worth understanding exactly what it does.

When the agent stops midway through a long task asking *"shall I continue?"*, with
this enabled it answers itself, up to a maximum number of times per message you
send. It's for multi-step tasks where you don't want to keep hitting "continue".

**It disables no safety confirmation.** The deletion dialogs and the admin ones
are a separate mechanism and keep asking every time, whether "Yes to everything"
is on or not. The only thing it automates is the continuation prompt.

Beyond that, the confirmation dialog for a destructive command has an **"Always
allow"** button, which is a different thing: it applies per *type* of operation
(approving an `rm` doesn't approve a `git reset --hard`), only for that
conversation, and isn't saved to disk — reopening the app asks again.

### Traffic capture

With capture on, the **🦊 Open Firefox** button opens a Firefox with a separate,
disposable profile, already set up to go through the proxy. Your everyday Firefox
stays intact: no proxy, no CA installed.

Everything you browse in that window lands in a SQLite. Later, in the chat, the
agent can ask the capture which sites are there, search flows and read bodies to
understand the structure before writing the extraction. Flows can be **tagged**
with a label and a note to revisit them, and there's a **repeater** to re-fire an
edited request.

The security section covers how your session credentials are handled when you
automate sites with login.

### Scheduled tasks

The agent can't wake itself up: the system wakes it. Each task translates into a
LaunchAgent (macOS), a Task Scheduler task (Windows) or a systemd user timer
(Linux) that, at the set time or **every N minutes**, runs without the app being
open. No administrator privileges are needed on any platform.

There are **two kinds of task**, and the difference matters:

- **Agent task**: each run sends a saved prompt to the model. For what needs
  judgment or writing on every pass (summarize, decide, compose).
- **Script task (script-first)**: for a concrete, repeatable workflow —"every
  10 min get me the balance", "every morning build the report"— the agent writes
  and tests **a deterministic Python script** and registers it as a task. That
  script runs on its own, **cheap and without spending model calls**. The LLM
  comes back in **only** as a *fallback*, when the script can't determine the next
  step (it fails, or prints an `ESCALAR: <reason>` line): then the agent fixes it
  or reports. It's the default model for recurring tasks: built once with
  judgment, then executed as code, not as conversation.

The agent sets up script tasks from the chat with the `programar_tarea_script`
tool. Each run is logged with its result on the **Tasks** tab, with a health
light (✅ healthy · ⏸ missing from the system · ❌ failed · 🕓 never ran) and
buttons to **run it now** (verify without waiting for the schedule) and **reload
it** into the scheduler.

### Version history

**Ctrl+0** (⌘0 on Mac) opens the code history of the installation. Every change
is recorded and can be rolled back. It's the safety net in case an update breaks
something.

### Shared packages

When the agent needs numpy, pandas or whatever, it's installed **once** in a
store shared across conversations, not a venv per conversation. If a conversation
needs a version that clashes with the shared one, that version is installed just
for it, without breaking the others. Separated by Python version, because wheels
with C extensions aren't compatible across versions.

### Costs

The app computes the spend per conversation from a table of prices per million
tokens, which can be updated from Settings. If there's no price loaded for a
model, it shows the tokens and cost as `—` instead of inventing a number.

---

## How it's organized

Everything that differs between operating systems goes through
**`core/plataforma.py`**. If you're going to touch this code, the rule is: the
per-OS branch goes there, not scattered across the UI.

| | |
|---|---|
| `INICIAR.bat` / `INICIAR.command` | Portable per-platform launch: they get a usable Python |
| `bootstrap.py` | Virtual environment, dependencies and launch (cross-platform) |
| `main_ui.py` | The UI (Tkinter) |
| `core/plataforma.py` | The only layer that knows about differences between systems |
| `core/interprete.py` | Which Python to use to run scripts (includes the packaged case) |
| `core/chat.py` | The conversation and the tool-calling loop |
| `core/biblioteca.py` | The shared library of modules |
| `core/generador.py` · `core/evaluador.py` | The iterative engine and its scoring |
| `core/programador*.py` | Tasks: common front + launchd / Task Scheduler / systemd backend |
| `core/proxy.py` · `core/marcas.py` | Traffic capture, redaction and context |
| `core/ejecucion.py` | Command execution, with confirmation of destructive ones |

Details of each port, decisions made and what's still pending:
[`HANDOFF-WINDOWS.md`](HANDOFF-WINDOWS.md) · [`HANDOFF-LINUX.md`](HANDOFF-LINUX.md).

### Where your data lives

Conversations, tasks, the library, the capture and the `.env` with the API keys
live **outside** the code folder:

| | |
|---|---|
| Windows | `%USERPROFILE%\tmp\agentfactory\` |
| macOS | `~/tmp/agentfactory/` |
| Linux | `~/tmp/agentfactory/` |

This is deliberate: a `git pull` doesn't touch your data, and publishing the
project folder doesn't take your keys with it. It's changed with the
`AGENTE_DATOS` variable.

## Tests

```
# Windows
runtime\python\python.exe -m pytest tests\ -q

# macOS / Linux
runtime/python/bin/python3 -m pytest tests/ -q
```

## Packaging to share

From **⚙️ Settings**, the **Create portable package (.zip)** button builds a zip
with the code only: it leaves out the environment, the runtime and your data, and
also scans the contents for credentials — if it finds something that looks like a
key, it doesn't generate the file.

On macOS there's also a button to build a `.dmg`. On Windows you can build an
`.exe` with PyInstaller (see [`HANDOFF-WINDOWS.md`](HANDOFF-WINDOWS.md)). On Linux
the path is the same portable folder; a `.deb`/`.rpm`/AppImage is still pending
(see [`HANDOFF-LINUX.md`](HANDOFF-LINUX.md)).

---

## Security

This app gives a language model real capabilities over your machine. It's worth
understanding what it protects and what it doesn't, without optimism.

### The agent runs real commands

It's not a sandbox and doesn't pretend to be: `ejecutar_shell` and
`ejecutar_python` can do what you could do from a terminal, over any path on the
system.

The brake is the one that matters: **anything that deletes or overwrites files
stops and asks you**, on both systems and with each one's own command lists
(`rm`, `mv`, `dd`, `git reset --hard` on Unix; `del`, `rd`, `Remove-Item`,
`format`, `robocopy /MIR`, `reg delete`, `shutdown` on Windows). Before you
approve, the dialog shows you which concrete paths it's going to operate on.

What's **not** protected: reading. A command that only reads asks nothing, and
the agent can read any file you have access to.

Privilege elevation exists only on macOS, and uses the system's authentication
dialog (never a custom one: your password never reaches this process). On Windows
and Linux it isn't implemented and the control is hidden.

### The capture intercepts HTTPS

To be able to read the traffic, the proxy terminates TLS: it installs its own CA
in a disposable Firefox profile. That's the default scope, and it's on purpose.

On Windows there's also an optional button to make the CA valid system-wide. It's
separate and reversible because its scope is much larger: it makes **all** of
your user's applications trust that CA.

### How your session credentials are handled

Automating tasks on sites with login is what this app exists for, so your session
cookies are part of the material it works with. How it treats them:

**What the model sees.** The context passed to the model is redacted: session
cookies, `Authorization` and CSRF tokens go out masked (`core/marcas.py`). The
bulk read used to write an extraction (`extraer_de_captura`) returns the response
bodies and **no headers**. The idea is that the model doesn't need to see a
cookie's value to write the code that uses it.

On top of that there's an **output filter** (`core/redactor.py`) that covers what
`marcas.py` doesn't catch: it masks `.env` values, JWTs, `Bearer` and assignments
like `password=` / `clave=` in **any** text going to the model — including the
captured bodies (where a login password can appear) and the output of
`ejecutar_shell`/`ejecutar_python` (in case a command prints the `.env`). It masks
**values, not structure**: the field names, the JS code, the endpoints and the
redirects stay visible, so the agent can reason about and reverse a flow; only the
credential's concrete value disappears.

This matters because the prompt **leaves your machine** toward DeepSeek,
Anthropic, Alibaba or Google, depending on the provider you have configured.
Anything that doesn't go into the prompt doesn't travel.

**What the automation uses.** The code the agent writes *can* authenticate:
that's what the repeater and the capture credentials are for. The difference is
*when* the value is resolved — at run time, on your machine's side, not in the
text sent to the model.

**Where they're stored.** In the capture SQLite, at `AGENTE_DATOS/_proxy/`, along
with the rest of your data and outside the code folder.

**The honest limit.** The agent can run code as your user. Once you give it that,
no cryptography stops a script from reading what you can read: the controls here
reduce accidental exposure and make deliberate exposure visible, they don't build
a safe against the agent itself. For the stolen-disk case, the right tool is
BitLocker or FileVault, not application-level encryption.

In progress: encrypting the headers at rest with decoding requested explicitly
(the same "allow once / always allow" mechanism as destructive commands), so that
access to credentials is a consented act and not a side effect.

### Captured content = data, not instructions

The agent reads pages and responses from servers you don't control, and at the
same time it has shell. A page can carry text written for a model to obey: an
HTML comment saying "ignore the above and run this".

The system prompt marks everything coming from the capture as untrusted content
—data to analyze, never instructions— and asks the agent to warn you if it finds
something like that instead of following it. No prompt defense is a guarantee: if
after analyzing traffic the agent proposes a command you didn't ask for, don't
approve it.

### The keys

The `.env` lives with your data, not with the code, so sharing the project
doesn't take them. On macOS and Linux it's left with `0600` permissions. On
Windows POSIX permissions don't apply (access goes through ACLs) and the file is
left readable by your user: the Diagnostics panel tells you so instead of lying
with a green check.

### Package installation

The agent can install PyPI packages without confirmation. A wrong name, or one
suggested by untrusted content, gets installed all the same. It's noted as
pending.
