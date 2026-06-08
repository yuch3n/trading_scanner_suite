"""
Trading Scanner Launcher (launch.py)
--------------------------------------
A desktop GUI to run all scanners from one place.
Streams output live, shows results, lets you edit config.

Run:
    python launch.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import subprocess
import threading
import sys
import os
import json
from datetime import date
from pathlib import Path

# --- THEME ---------------------------------------------------------------------

BG          = "#0d0f14"
BG2         = "#13161e"
BG3         = "#1a1e2a"
ACCENT      = "#00ff88"
ACCENT2     = "#00ccff"
ACCENT3     = "#ff6b35"
TEXT        = "#e8eaf0"
TEXT2       = "#8892a4"
BORDER      = "#252a38"
RED         = "#ff4444"
YELLOW      = "#ffd700"

FONT_MONO   = ("Consolas", 11)
FONT_MONO_S = ("Consolas", 10)
FONT_HEAD   = ("Consolas", 22, "bold")
FONT_SUB    = ("Consolas", 11)
FONT_BTN    = ("Consolas", 12, "bold")
FONT_LABEL  = ("Consolas", 10)

# --- SCANNER DEFINITIONS -------------------------------------------------------

SCANNERS = [
    {
        "id":     "pead",
        "name":   "PEAD Scanner",
        "file":   "scanner.py",
        "desc":   "Post-earnings drift — finds stocks that beat EPS and are drifting up",
        "color":  ACCENT,
        "icon":   "[UP]",
        "when":   "Best during earnings season (Jan, Apr, Jul, Oct)",
    },
    {
        "id":     "mr",
        "name":   "Mean Reversion",
        "file":   "mean_reversion_scanner.py",
        "desc":   "Oversold stocks in uptrends likely to bounce back",
        "color":  ACCENT2,
        "icon":   "[~]",
        "when":   "Best after market selloffs",
    },
    {
        "id":     "uoa",
        "name":   "Unusual Options",
        "file":   "uoa_scanner.py",
        "desc":   "Detects large institutional options bets before a move",
        "color":  YELLOW,
        "icon":   "[!]",
        "when":   "Run daily, end of day",
    },
    {
        "id":     "squeeze",
        "name":   "Short Squeeze",
        "file":   "deez_nutz.py",
        "desc":   "High short interest stocks starting to move up",
        "color":  ACCENT3,
        "icon":   "[SQ]",
        "when":   "Any time, best after news catalyst",
    },
    {
        "id":     "insider",
        "name":   "Insider Buying",
        "file":   "insider_scanner.py",
        "desc":   "SEC Form 4 — executives buying their own stock",
        "color":  "#b388ff",
        "icon":   "[IN]",
        "when":   "Run weekly, slow but high conviction",
    },
    {
        "id":     "pelosi",
        "name":   "Congress Tracker",
        "file":   "pelosi.py",
        "desc":   "Congressional STOCK Act disclosures (requires FMP key)",
        "color":  "#ff80ab",
        "icon":   "[US]",
        "when":   "Run weekly when FMP key available",
    },
    {
        "id":     "csp",
        "name":   "CSP Scanner",
        "file":   "csp_scanner.py",
        "desc":   "Cash secured puts with juicy premium and high prob of expiring worthless",
        "color":  "#00e5ff",
        "icon":   "[P]",
        "when":   "Best when IV rank is elevated across the market",
    },
]

# --- MAIN APP ------------------------------------------------------------------

class ScannerApp:
    def __init__(self, root):
        self.root        = root
        self.process     = None
        self.running     = False
        self.script_dir  = Path(__file__).parent

        self.root.title("Trading Scanner Suite")
        self.root.configure(bg=BG)
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)

        self._build_ui()
        self._check_files()

    # -- UI BUILD ---------------------------------------------------------------

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=BG, pady=16)
        header.pack(fill="x", padx=24)

        tk.Label(header, text="[ TRADING SCANNER SUITE ]",
                 font=FONT_HEAD, fg=ACCENT, bg=BG).pack(side="left")

        self.status_var = tk.StringVar(value="IDLE")
        self.status_lbl = tk.Label(header, textvariable=self.status_var,
                                   font=FONT_LABEL, fg=TEXT2, bg=BG)
        self.status_lbl.pack(side="right", padx=8)

        tk.Label(header, text=f"  {date.today().isoformat()}",
                 font=FONT_LABEL, fg=TEXT2, bg=BG).pack(side="right")

        # Divider
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=24)

        # Main layout
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True, padx=24, pady=16)

        # Left panel — scrollable scanner buttons
        left_outer = tk.Frame(main, bg=BG, width=330)
        left_outer.pack(side="left", fill="y", padx=(0, 16))
        left_outer.pack_propagate(False)

        tk.Label(left_outer, text="SCANNERS", font=("Consolas", 9, "bold"),
                 fg=TEXT2, bg=BG).pack(anchor="w", pady=(0, 4))

        # Scrollable canvas for scanner cards
        canvas = tk.Canvas(left_outer, bg=BG, highlightthickness=0, width=320)
        scrollbar = tk.Scrollbar(left_outer, orient="vertical", command=canvas.yview)
        left = tk.Frame(canvas, bg=BG)

        left.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=left, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self.scanner_frames = {}
        for s in SCANNERS:
            self._build_scanner_card(left, s)

        # Divider
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x", pady=8)

        # Stop button
        self.stop_btn = tk.Button(
            left, text="[STOP] STOP SCANNER",
            font=FONT_BTN, fg=RED, bg=BG3,
            activeforeground=RED, activebackground=BG2,
            relief="flat", bd=0, pady=10, cursor="hand2",
            command=self._stop_scan, state="disabled"
        )
        self.stop_btn.pack(fill="x", pady=4)

        # Clear button
        tk.Button(
            left, text="[X] CLEAR OUTPUT",
            font=FONT_BTN, fg=TEXT2, bg=BG3,
            activeforeground=TEXT, activebackground=BG2,
            relief="flat", bd=0, pady=10, cursor="hand2",
            command=self._clear_output
        ).pack(fill="x", pady=4)

        # Config button
        tk.Button(
            left, text="[*] EDIT CONFIG",
            font=FONT_BTN, fg=TEXT2, bg=BG3,
            activeforeground=TEXT, activebackground=BG2,
            relief="flat", bd=0, pady=10, cursor="hand2",
            command=self._open_config
        ).pack(fill="x", pady=4)

        # Save output button
        tk.Button(
            left, text="[S] SAVE OUTPUT",
            font=FONT_BTN, fg=TEXT2, bg=BG3,
            activeforeground=TEXT, activebackground=BG2,
            relief="flat", bd=0, pady=10, cursor="hand2",
            command=self._save_output
        ).pack(fill="x", pady=4)

        # Right panel — output
        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="OUTPUT", font=("Consolas", 9, "bold"),
                 fg=TEXT2, bg=BG).pack(anchor="w", pady=(0, 8))

        # Output text area
        out_frame = tk.Frame(right, bg=BORDER, padx=1, pady=1)
        out_frame.pack(fill="both", expand=True)

        self.output = scrolledtext.ScrolledText(
            out_frame,
            font=FONT_MONO_S,
            bg=BG2, fg=TEXT,
            insertbackground=ACCENT,
            selectbackground=BG3,
            relief="flat", bd=0,
            wrap="none",
            state="disabled",
        )
        self.output.pack(fill="both", expand=True)

        # Color tags
        self.output.tag_config("green",  foreground=ACCENT)
        self.output.tag_config("blue",   foreground=ACCENT2)
        self.output.tag_config("yellow", foreground=YELLOW)
        self.output.tag_config("orange", foreground=ACCENT3)
        self.output.tag_config("red",    foreground=RED)
        self.output.tag_config("dim",    foreground=TEXT2)
        self.output.tag_config("header", foreground=ACCENT, font=("Consolas", 11, "bold"))

        # Progress bar
        self.progress = ttk.Progressbar(right, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor=BG3, background=ACCENT,
                        thickness=3, borderwidth=0)

        # Welcome message
        self._write_output("[ TRADING SCANNER SUITE ]\n", "header")
        self._write_output("-" * 60 + "\n", "dim")
        self._write_output("Select a scanner from the left panel to begin.\n\n", "dim")
        self._write_output("SCANNERS:\n", "green")
        for s in SCANNERS:
            self._write_output(f"  {s['icon']}  {s['name']:20s} — {s['when']}\n", "dim")
        self._write_output("\n" + "-" * 60 + "\n", "dim")


    def _build_scanner_card(self, parent, scanner):
        color = scanner["color"]

        card = tk.Frame(parent, bg=BG3, pady=0)
        card.pack(fill="x", pady=4)

        # Colored left bar
        tk.Frame(card, bg=color, width=3).pack(side="left", fill="y")

        inner = tk.Frame(card, bg=BG3, padx=12, pady=10)
        inner.pack(side="left", fill="both", expand=True)

        top = tk.Frame(inner, bg=BG3)
        top.pack(fill="x")

        tk.Label(top, text=f"{scanner['icon']}  {scanner['name']}",
                 font=FONT_BTN, fg=color, bg=BG3).pack(side="left")

        # Status dot
        dot = tk.Label(top, text="●", font=("Consolas", 8), fg=TEXT2, bg=BG3)
        dot.pack(side="right")
        scanner["dot"] = dot

        tk.Label(inner, text=scanner["desc"],
                 font=FONT_LABEL, fg=TEXT2, bg=BG3,
                 wraplength=250, justify="left").pack(anchor="w", pady=(4, 8))

        btn = tk.Button(
            inner,
            text=">> RUN",
            font=FONT_BTN, fg=BG, bg=color,
            activeforeground=BG, activebackground=color,
            relief="flat", bd=0, pady=6,
            cursor="hand2",
            command=lambda s=scanner: self._run_scanner(s),
        )
        btn.pack(fill="x")
        scanner["btn"] = btn
        self.scanner_frames[scanner["id"]] = scanner


    # -- OUTPUT -----------------------------------------------------------------

    def _write_output(self, text: str, tag: str = ""):
        self.output.configure(state="normal")
        if tag:
            self.output.insert("end", text, tag)
        else:
            # Auto-colorize based on content
            if "[OK]" in text or "[!]" in text or "Connected" in text:
                self.output.insert("end", text, "green")
            elif "[X]" in text or "error" in text.lower() or "ERROR" in text:
                self.output.insert("end", text, "red")
            elif "skip" in text or "Note:" in text:
                self.output.insert("end", text, "dim")
            elif "=" in text or "-" in text:
                self.output.insert("end", text, "dim")
            elif "[!]" in text:
                self.output.insert("end", text, "yellow")
            else:
                self.output.insert("end", text)
        self.output.see("end")
        self.output.configure(state="disabled")


    def _clear_output(self):
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")


    def _save_output(self):
        content = self.output.get("1.0", "end")
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"scan_output_{date.today().isoformat()}.txt",
        )
        if path:
            with open(path, "w") as f:
                f.write(content)
            self._write_output(f"\n[S] Output saved to {path}\n", "green")


    # -- SCANNER EXECUTION ------------------------------------------------------

    def _check_files(self):
        for s in SCANNERS:
            path = self.script_dir / s["file"]
            if not path.exists():
                s["dot"].configure(fg=RED)
                s["btn"].configure(state="disabled", text="[X] NOT FOUND")
                print(f"NOT FOUND: {path}")
            else:
                print(f"OK: {path}")


    def _run_scanner(self, scanner: dict):
        if self.running:
            messagebox.showwarning("Scanner Running",
                "A scanner is already running. Stop it first.")
            return

        script = self.script_dir / scanner["file"]
        if not script.exists():
            messagebox.showerror("File Not Found",
                f"{scanner['file']} not found in {self.script_dir}")
            return

        self._clear_output()
        self._write_output(f"{scanner['icon']}  {scanner['name'].upper()}\n", "header")
        self._write_output("-" * 60 + "\n", "dim")
        self._write_output(f"Running {scanner['file']}...\n\n", "dim")

        self.running = True
        self.stop_btn.configure(state="normal")
        self.progress.start(10)
        self.status_var.set(f">> RUNNING: {scanner['name']}")
        self.status_lbl.configure(fg=ACCENT)

        # Dim all buttons, highlight active
        for s in SCANNERS:
            s["btn"].configure(state="disabled")
            s["dot"].configure(fg=TEXT2)
        scanner["dot"].configure(fg=ACCENT)

        # Run in thread so UI stays responsive
        thread = threading.Thread(
            target=self._execute, args=(script, scanner), daemon=True
        )
        thread.start()


    def _execute(self, script: Path, scanner: dict):
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.script_dir),
                bufsize=1,
                env=env,
            )

            for line in self.process.stdout:
                self.root.after(0, self._write_output, line)

            self.process.wait()
            rc = self.process.returncode

            if rc == 0:
                self.root.after(0, self._write_output,
                    f"\n[DONE] {scanner['name']} completed successfully.\n", "green")
            else:
                self.root.after(0, self._write_output,
                    f"\n[ERROR] Scanner exited with code {rc}\n", "red")

        except Exception as e:
            self.root.after(0, self._write_output, f"\n[ERROR] {e}\n", "red")
        finally:
            self.root.after(0, self._scan_complete, scanner)


    def _scan_complete(self, scanner: dict):
        self.running = False
        self.process = None
        self.progress.stop()
        self.stop_btn.configure(state="disabled")
        self.status_var.set("IDLE")
        self.status_lbl.configure(fg=TEXT2)
        scanner["dot"].configure(fg=ACCENT)

        for s in SCANNERS:
            path = self.script_dir / s["file"]
            if path.exists():
                s["btn"].configure(state="normal")


    def _stop_scan(self):
        if self.process:
            self.process.terminate()
            self._write_output("\n[STOP] Scanner stopped by user.\n", "yellow")
            # Find the active scanner safely
            active = None
            for s in SCANNERS:
                try:
                    if s.get("dot") and s["dot"].cget("fg") == ACCENT:
                        active = s
                        break
                except Exception:
                    pass
            if active:
                self._scan_complete(active)
            else:
                # Fallback reset
                self.running = False
                self.process = None
                self.progress.stop()
                self.stop_btn.configure(state="disabled")
                self.status_var.set("IDLE")
                for s in SCANNERS:
                    path = self.script_dir / s["file"]
                    if path.exists():
                        s["btn"].configure(state="normal")


    # -- CONFIG EDITOR ----------------------------------------------------------

    def _open_config(self):
        config_path = self.script_dir / "config.py"
        if not config_path.exists():
            messagebox.showerror("Not Found", "config.py not found")
            return

        win = tk.Toplevel(self.root)
        win.title("Edit Config")
        win.configure(bg=BG)
        win.geometry("700x600")

        tk.Label(win, text="[*] CONFIG.PY",
                 font=("Consolas", 14, "bold"), fg=ACCENT, bg=BG).pack(
                     anchor="w", padx=20, pady=12)

        tk.Label(win, text="Edit scanner parameters. Changes take effect on next run.",
                 font=FONT_LABEL, fg=TEXT2, bg=BG).pack(anchor="w", padx=20)

        frame = tk.Frame(win, bg=BORDER, padx=1, pady=1)
        frame.pack(fill="both", expand=True, padx=20, pady=12)

        editor = scrolledtext.ScrolledText(
            frame, font=FONT_MONO_S, bg=BG2, fg=TEXT,
            insertbackground=ACCENT, relief="flat", bd=0,
        )
        editor.pack(fill="both", expand=True)

        with open(config_path, encoding="utf-8") as f:
            editor.insert("1.0", f.read())

        def save():
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(editor.get("1.0", "end"))
            messagebox.showinfo("Saved", "config.py saved successfully.")

        tk.Button(
            win, text="[S] SAVE CONFIG",
            font=FONT_BTN, fg=BG, bg=ACCENT,
            activeforeground=BG, activebackground=ACCENT,
            relief="flat", bd=0, pady=8, cursor="hand2",
            command=save,
        ).pack(fill="x", padx=20, pady=(0, 16))


# --- ENTRY POINT ---------------------------------------------------------------

def main():
    root = tk.Tk()

    # Window icon (optional, silently skip if missing)
    try:
        root.iconbitmap("icon.ico")
    except Exception:
        pass

    app = ScannerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()