#!/usr/bin/env python3
"""
Picurate GUI Installer
======================
Run with:  python3 installer.py
Requires:  Python 3.12+  (Tkinter is included with Python — no other deps needed yet)

Steps shown to the user:
  1. Welcome
  2. Dependency install (with live log)
  3. Desktop launcher (Linux) or shortcut (Windows)
  4. Done
"""
from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

APP_NAME = "Picurate"
APP_VERSION = "0.1.0"
DIR = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"
IS_LIN = platform.system() == "Linux"

REQUIRED_PYTHON = (3, 12)


# ── Helpers ───────────────────────────────────────────────────────────────────

def python_ok() -> bool:
    return sys.version_info >= REQUIRED_PYTHON


def venv_python() -> Path:
    if IS_WIN:
        return DIR / ".venv" / "Scripts" / "python.exe"
    return DIR / ".venv" / "bin" / "python3"


def venv_pip() -> Path:
    if IS_WIN:
        return DIR / ".venv" / "Scripts" / "pip.exe"
    return DIR / ".venv" / "bin" / "pip"


# ── Main installer window ─────────────────────────────────────────────────────

class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION} — Setup")
        self.resizable(False, False)
        self.geometry("560x440")
        self._center()
        self._set_icon()

        self._pages: list[tk.Frame] = []
        self._current = 0
        self._install_ok = False
        self._launcher_installed = False

        container = tk.Frame(self, bg="#f5f5f5")
        container.pack(fill="both", expand=True)

        # Header strip
        self._header = tk.Label(
            container,
            text="",
            font=("Helvetica", 16, "bold"),
            bg="#1d3557",
            fg="white",
            anchor="w",
            padx=20,
            pady=14,
        )
        self._header.pack(fill="x")

        # Page area
        self._page_area = tk.Frame(container, bg="#f5f5f5")
        self._page_area.pack(fill="both", expand=True, padx=0, pady=0)

        # Navigation strip
        nav = tk.Frame(container, bg="#e0e0e0", pady=8)
        nav.pack(fill="x", side="bottom")
        self._btn_back = ttk.Button(nav, text="← Back", command=self._go_back)
        self._btn_back.pack(side="left", padx=16)
        self._btn_next = ttk.Button(nav, text="Next →", command=self._go_next)
        self._btn_next.pack(side="right", padx=16)

        # Build all pages
        self._pages = [
            self._page_welcome(),
            self._page_install(),
            self._page_launcher(),
            self._page_done(),
        ]
        self._show_page(0)

    def _center(self):
        self.update_idletasks()
        w, h = 560, 440
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _set_icon(self):
        ico = DIR / "assets" / "icon" / "picurate.png"
        if ico.exists():
            try:
                img = tk.PhotoImage(file=str(ico))
                self.iconphoto(True, img)
                self._icon_ref = img  # keep ref
            except Exception:
                pass

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_welcome(self) -> tk.Frame:
        f = tk.Frame(self._page_area, bg="#f5f5f5")

        tk.Label(f, text=f"Welcome to {APP_NAME}!", font=("Helvetica", 15, "bold"),
                 bg="#f5f5f5", fg="#1d3557").pack(pady=(28, 6))
        tk.Label(
            f,
            text=(
                "A local, private photo organizer — sort by people,\n"
                "places, and topics. No cloud. No subscriptions.\n\n"
                "This wizard will:\n"
                "  1.  Check Python requirements\n"
                "  2.  Install all dependencies\n"
                "  3.  Add a desktop launcher\n\n"
            ),
            font=("Helvetica", 11),
            bg="#f5f5f5",
            justify="left",
        ).pack(padx=40, anchor="w")

        # Python check inline
        ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ok = python_ok()
        color = "#1a7a3c" if ok else "#cc0000"
        icon = "✓" if ok else "✗"
        tk.Label(
            f,
            text=f"  {icon}  Python {ver} {'— OK' if ok else '— Python 3.12+ required'}",
            font=("Helvetica", 11, "bold"),
            bg="#f5f5f5",
            fg=color,
        ).pack(padx=40, anchor="w")

        if not ok:
            tk.Label(
                f,
                text="  Download Python 3.12+ from https://python.org before continuing.",
                font=("Helvetica", 10),
                bg="#f5f5f5",
                fg="#555",
            ).pack(padx=40, anchor="w")

        return f

    def _page_install(self) -> tk.Frame:
        f = tk.Frame(self._page_area, bg="#f5f5f5")
        tk.Label(f, text="Installing dependencies…", font=("Helvetica", 12),
                 bg="#f5f5f5").pack(pady=(18, 6), padx=20, anchor="w")

        self._log = tk.Text(f, height=14, state="disabled",
                             bg="#1e1e1e", fg="#d4d4d4",
                             font=("Courier", 9), relief="flat")
        self._log.pack(fill="both", expand=True, padx=16, pady=4)

        self._progress = ttk.Progressbar(f, mode="indeterminate")
        self._progress.pack(fill="x", padx=16, pady=(0, 10))

        return f

    def _page_launcher(self) -> tk.Frame:
        f = tk.Frame(self._page_area, bg="#f5f5f5")
        tk.Label(f, text="Desktop launcher", font=("Helvetica", 13, "bold"),
                 bg="#f5f5f5", fg="#1d3557").pack(pady=(24, 10))

        if IS_LIN:
            tk.Label(
                f,
                text=(
                    "Add Picurate to your application menu and taskbar?\n\n"
                    "This installs a .desktop entry and copies the icon\n"
                    "to the system icon theme."
                ),
                font=("Helvetica", 11),
                bg="#f5f5f5",
                justify="left",
            ).pack(padx=40, anchor="w")
            self._launcher_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                f, text="Install desktop launcher",
                variable=self._launcher_var
            ).pack(padx=40, pady=12, anchor="w")

        elif IS_WIN:
            tk.Label(
                f,
                text=(
                    "Create a desktop shortcut for Picurate?\n\n"
                    "You can also launch Picurate by double-clicking\n"
                    "run.bat in the installation folder."
                ),
                font=("Helvetica", 11),
                bg="#f5f5f5",
                justify="left",
            ).pack(padx=40, anchor="w")
            self._launcher_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                f, text="Create desktop shortcut",
                variable=self._launcher_var
            ).pack(padx=40, pady=12, anchor="w")

        else:
            tk.Label(
                f,
                text="Run Picurate with:  python3 main.py\nor use run.sh",
                font=("Helvetica", 11),
                bg="#f5f5f5",
            ).pack(padx=40, pady=20, anchor="w")
            self._launcher_var = tk.BooleanVar(value=False)

        return f

    def _page_done(self) -> tk.Frame:
        f = tk.Frame(self._page_area, bg="#f5f5f5")
        self._done_icon = tk.Label(f, text="✓", font=("Helvetica", 48),
                                    bg="#f5f5f5", fg="#1a7a3c")
        self._done_icon.pack(pady=(30, 4))
        self._done_label = tk.Label(
            f,
            text="Picurate is ready!",
            font=("Helvetica", 14, "bold"),
            bg="#f5f5f5",
            fg="#1d3557",
        )
        self._done_label.pack()
        self._done_sub = tk.Label(
            f,
            text="Click Launch to start using Picurate.",
            font=("Helvetica", 11),
            bg="#f5f5f5",
            fg="#555",
        )
        self._done_sub.pack(pady=6)

        self._btn_launch = ttk.Button(f, text="Launch Picurate", command=self._launch)
        self._btn_launch.pack(pady=16)
        return f

    # ── Navigation ────────────────────────────────────────────────────────────

    _TITLES = ["Welcome", "Installing", "Desktop Launcher", "Finished"]

    def _show_page(self, idx: int) -> None:
        for p in self._pages:
            p.pack_forget()
        self._pages[idx].pack(fill="both", expand=True)
        self._current = idx
        self._header.config(text=f"  {APP_NAME} Setup  —  {self._TITLES[idx]}")
        self._btn_back.config(state="normal" if idx > 0 else "disabled")
        last = idx == len(self._pages) - 1
        self._btn_next.config(
            text="Finish" if last else "Next →",
            state="normal" if not last else "disabled",
        )

    def _go_next(self) -> None:
        nxt = self._current + 1
        if nxt >= len(self._pages):
            self.destroy()
            return

        if self._current == 0:
            if not python_ok():
                messagebox.showerror(
                    "Python version",
                    f"Python 3.12+ is required.\nCurrent: {sys.version}"
                )
                return
            self._show_page(1)
            self._run_install()

        elif self._current == 1:
            if not self._install_ok:
                messagebox.showerror(
                    "Installation incomplete",
                    "Dependencies did not install successfully. Check the log above."
                )
                return
            self._show_page(2)

        elif self._current == 2:
            self._install_launcher()
            self._show_page(3)

        else:
            self._show_page(nxt)

    def _go_back(self) -> None:
        if self._current > 0:
            self._show_page(self._current - 1)

    # ── Install logic ─────────────────────────────────────────────────────────

    def _log_append(self, text: str) -> None:
        self._log.config(state="normal")
        self._log.insert("end", text)
        self._log.see("end")
        self._log.config(state="disabled")

    def _run_install(self) -> None:
        self._progress.start(10)
        self._btn_next.config(state="disabled")
        self._btn_back.config(state="disabled")
        q: queue.Queue[str | None] = queue.Queue()

        def worker():
            try:
                # Create venv if needed
                py_exec = venv_python()
                if not py_exec.exists():
                    q.put("[1/2] Creating virtual environment…\n")
                    r = subprocess.run(
                        [sys.executable, "-m", "venv", str(DIR / ".venv")],
                        capture_output=True, text=True,
                    )
                    if r.returncode != 0:
                        q.put(f"ERROR: {r.stderr}\n")
                        q.put(None)
                        return
                    q.put("      Virtual environment created.\n")

                # Install deps
                q.put("[2/2] Installing dependencies (this may take a few minutes)…\n")
                req = DIR / "requirements.txt"
                proc = subprocess.Popen(
                    [str(venv_pip()), "install", "-r", str(req), "--progress-bar", "off"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    q.put(line)
                proc.wait()
                if proc.returncode != 0:
                    q.put("\nERROR: pip install failed. Check the output above.\n")
                    q.put(None)
                    return
                q.put("\nDependencies installed successfully.\n")
                q.put("DONE_OK")
                q.put(None)
            except Exception as exc:
                q.put(f"ERROR: {exc}\n")
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                while True:
                    msg = q.get_nowait()
                    if msg is None:
                        break
                    if msg == "DONE_OK":
                        self._install_ok = True
                        continue
                    self._log_append(msg)
            except queue.Empty:
                pass
            if self._install_ok or not q.empty():
                pass  # still draining
            # Check if worker finished
            if self._install_ok:
                self._progress.stop()
                self._progress.config(mode="determinate", value=100)
                self._btn_next.config(state="normal")
                self._btn_back.config(state="normal")
                return
            # Check error (None sentinel in queue means done)
            self.after(150, poll)

        self.after(150, poll)

    # ── Launcher install ──────────────────────────────────────────────────────

    def _install_launcher(self) -> None:
        if not getattr(self, "_launcher_var", None) or not self._launcher_var.get():
            return
        try:
            if IS_LIN:
                subprocess.run(
                    ["bash", str(DIR / "install_launcher.sh")],
                    capture_output=True,
                )
                self._launcher_installed = True
            elif IS_WIN:
                self._install_windows_shortcut()
                self._launcher_installed = True
        except Exception as exc:
            messagebox.showwarning("Launcher", f"Could not install launcher:\n{exc}")

    def _install_windows_shortcut(self) -> None:
        run_bat = DIR / "run.bat"
        ico = DIR / "assets" / "icon" / "picurate.ico"
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        vbs = (
            f'Set oWS = WScript.CreateObject("WScript.Shell")\n'
            f'sLinkFile = "{desktop}\\Picurate.lnk"\n'
            f'Set oLink = oWS.CreateShortcut(sLinkFile)\n'
            f'oLink.TargetPath = "{run_bat}"\n'
            f'oLink.WorkingDirectory = "{DIR}"\n'
            f'oLink.IconLocation = "{ico}"\n'
            f'oLink.Description = "Picurate Photo Organizer"\n'
            f'oLink.Save\n'
        )
        vbs_path = Path(os.environ.get("TEMP", "/tmp")) / "picurate_shortcut.vbs"
        vbs_path.write_text(vbs)
        subprocess.run(["cscript", "//nologo", str(vbs_path)], capture_output=True)
        vbs_path.unlink(missing_ok=True)

    # ── Launch ────────────────────────────────────────────────────────────────

    def _launch(self) -> None:
        py = venv_python()
        if not py.exists():
            py = Path(sys.executable)
        try:
            if IS_WIN:
                subprocess.Popen(
                    [str(py), str(DIR / "main.py")],
                    creationflags=subprocess.DETACHED_PROCESS,
                )
            else:
                subprocess.Popen(
                    [str(py), str(DIR / "main.py")],
                    start_new_session=True,
                )
        except Exception as exc:
            messagebox.showerror("Launch failed", str(exc))
            return
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = InstallerApp()
    app.mainloop()
