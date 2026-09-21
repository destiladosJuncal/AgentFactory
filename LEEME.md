# AgentFactory - portable

This package ships **only the code**. It includes no keys and no conversations:
when you open it, you'll configure your own API keys from the app.

**You don't need Python installed.** If this computer doesn't have a usable one,
the launcher downloads one and drops it inside this same folder. It installs
nothing on the system and asks for no administrator password: if you delete the
folder, no trace is left.

## Windows 10 / 11

1. Unzip the **whole** archive into a folder of yours
   (for example `C:\Users\<you>\AgentFactory`).
2. Double-click **INICIAR.bat**.

The first time a black window opens showing the setup: it downloads Python
(~30 MB) and installs the dependencies. It takes a couple of minutes and needs
internet. When it finishes, the app opens on its own and the black window closes.
Subsequent launches are direct.

Two expected notices the first time:

  - **"Windows protected your PC"** (SmartScreen). It's because the file isn't
    signed. Click *More info* -> *Run anyway*.
  - If you unzipped with File Explorer and the `.bat` won't start, right-click
    it -> *Properties* -> tick **Unblock** -> OK.

Important: unzip **before** running. If you double-click the `.bat` from inside
the zip, Windows runs it in a temporary folder and the installation is lost.

The `python.exe` that Windows ships on the PATH **is not Python**: it's a shortcut
that opens the Microsoft Store. The launcher detects it and doesn't use it.

To see the detail of a launch that fails, run `INICIAR.bat` from a console, or
look at the log in `%USERPROFILE%\tmp\agentfactory\_logs\ui.log`.

## macOS

1. Unzip the archive.
2. Double-click **INICIAR.command**.

The first time macOS will say it can't verify the developer: the app isn't signed
with an Apple Developer ID. To open it:

  - Right-click **INICIAR.command** -> **Open** -> Open.
  - If macOS doesn't offer "Open": System Settings -> Privacy & Security ->
    scroll down to the notice -> "Open Anyway".

Or from the Terminal, once:

    xattr -dr com.apple.quarantine "<unzipped folder>"

Note: the `/usr/bin/python3` that macOS ships **is not Python**, it's a shortcut
that opens the installer for Apple's developer tools. The launcher detects it and
doesn't use it.

## Requirements

  - Windows 10 (1803 or later) / 11 64-bit, or a Mac with Apple Silicon
  - Internet connection the first time

## Your data

Conversations, tasks and the library are saved **outside** this folder:

    Windows   %USERPROFILE%\tmp\agentfactory\
    macOS     ~/tmp/agentfactory/

The API keys go in the `.env` in that folder, not in the code folder, so if you
share the zip again they don't travel with it. Replacing this folder with a new
version of the app doesn't touch your data.
