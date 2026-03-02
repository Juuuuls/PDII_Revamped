# PDII_rev_SF_touch7_scroll_popup_instagram_link.py
# Touch UI + LEFT Capture panel scroll (7" 1024x600) + POPUPS
# Instagram-inspired SOFT palette + Instagram-style gradient bars
# Backend: dgpo5.py
#
# Added:
# ✅ Popup on Capture completion (success/fail)
# ✅ Popup on Deploy completion (success/fail)
# ✅ Deploy success popup includes: [Close] and [Go to Simulation] (opens link)
#
# NOTE: UI changes only. No backend logic/functions altered.

import os
import sys
import subprocess
import threading
import time
import platform
import re
import tkinter as tk
import webbrowser

try:
    import customtkinter as ctk
except ModuleNotFoundError:
    raise SystemExit("❌ Please install CustomTkinter:\n  pip install customtkinter")

# optional for port scan
try:
    import serial.tools.list_ports as list_ports
except Exception:
    list_ports = None

# deploy deps
try:
    import pandas as pd
except Exception:
    pd = None

try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

try:
    import joblib
except Exception:
    joblib = None


# ---------------- Paths / constants ----------------
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dgpo5.py")

ANGLE_STEP = 1.8
MAX_ROWS = 200
DEFAULT_CEILING_OFFSET = 100.0

SIMULATION_URL = "https://marichuesplmbrg.github.io/Vibra-Web/#/simulation"

# ---------------- Instagram-inspired SOFT palette ----------------
# (Softened for eye comfort on 7" LCD)
IG_PINK = "#f05ea8"
IG_PURPLE = "#a06bff"
IG_ORANGE = "#f6b15a"
IG_YELLOW = "#f6d86b"

ACCENT = IG_PINK
ACCENT_HOVER = "#f37bb8"

# Avoid pure black for eye comfort
BG = "#111216"
PANEL = "#161720"
PANEL_2 = "#1c1e2a"
FIELD = "#12131a"

TEXT = "#eef0ff"
TEXT_DIM = "#b8bbd1"

# Touch sizing
FONT_TITLE = 20
FONT_SUB = 13
FONT_LABEL = 14
FONT_MONO = 11

BTN_H = 54
BTN_H_SMALL = 46
ENTRY_H = 44

PAD_X = 12
PAD_Y = 10
RADIUS = 14


# ----------------- Classification helpers -----------------
def classify_rt60_rule(rt: float) -> str:
    try:
        r = float(rt)
    except Exception:
        return ""
    if r < 0.2:
        return "Dead Spot"
    elif r <= 0.4:
        return "Neutral Zone"
    else:
        return "Hot Spot"


def _load_model_any(path: str):
    if joblib is None:
        raise RuntimeError("joblib not installed")
    obj = joblib.load(path)
    if isinstance(obj, dict) and "model" in obj:
        return obj["model"]
    return obj


def _ensure_canonical_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    work = df.copy()

    if "angle" not in work.columns:
        for alt in ("number", "Angle", "id", "ID"):
            if alt in work.columns:
                work.rename(columns={alt: "angle"}, inplace=True)
                break

    if "reverberation" not in work.columns:
        for alt in ("rt60", "RT60", "Reverberation", "Rt60"):
            if alt in work.columns:
                work.rename(columns={alt: "reverberation"}, inplace=True)
                break

    if "ultrasonicValue" not in work.columns:
        for alt in ("utv", "Ultrasonic Value", "Ultrasonic", "ultrasonic"):
            if alt in work.columns:
                work.rename(columns={alt: "ultrasonicValue"}, inplace=True)
                break

    if "db" not in work.columns:
        for alt in ("dB", "DB", "decibel"):
            if alt in work.columns:
                work.rename(columns={alt: "db"}, inplace=True)
                break

    if "Classification" not in work.columns:
        for alt in ("class", "Class", "classification", "CLASS"):
            if alt in work.columns:
                work.rename(columns={alt: "Classification"}, inplace=True)
                break

    return work


def _predict_with_model(model, df: "pd.DataFrame", log_fn):
    work = _ensure_canonical_columns(df)
    want = list(getattr(model, "feature_names_in_", []))

    if "RT60" in want and "RT60" not in work.columns and "reverberation" in work.columns:
        work["RT60"] = work["reverberation"]
    if "rt60" in want and "rt60" not in work.columns and "reverberation" in work.columns:
        work["rt60"] = work["reverberation"]

    if "utv" in want and "utv" not in work.columns and "ultrasonicValue" in work.columns:
        work["utv"] = work["ultrasonicValue"]

    if "RT60_deviation" in want and "RT60_deviation" not in work.columns:
        base = None
        if "RT60" in work.columns:
            base = work["RT60"]
        elif "rt60" in work.columns:
            base = work["rt60"]
        elif "reverberation" in work.columns:
            base = work["reverberation"]
        if base is None:
            raise ValueError("Missing RT60/reverberation for RT60_deviation.")
        work["RT60_deviation"] = (base.astype(float) - 0.3).abs()

    if "frequency" in want and "frequency" not in work.columns:
        work["frequency"] = 1000.0

    if want:
        try:
            X = work[want].astype(float)
            log_fn(f"→ Using model features: {want}")
            return model.predict(X)
        except Exception as e:
            log_fn(f"…could not use feature_names_in_ {want}: {e}")

    if "reverberation" in work.columns:
        X = work[["reverberation"]].astype(float).values
        log_fn("→ Using features: ['reverberation']")
        return model.predict(X)

    if all(c in work.columns for c in ["ultrasonicValue", "reverberation"]):
        X = work[["ultrasonicValue", "reverberation"]].astype(float).values
        log_fn("→ Using features: ['ultrasonicValue','reverberation']")
        return model.predict(X)

    base = None
    if "reverberation" in work.columns:
        base = work["reverberation"]
    elif "RT60" in work.columns:
        base = work["RT60"]
    elif "rt60" in work.columns:
        base = work["rt60"]

    if base is not None:
        tmp = work.copy()
        if "frequency" not in tmp.columns:
            tmp["frequency"] = 1000.0
        if "RT60_deviation" not in tmp.columns:
            tmp["RT60_deviation"] = (base.astype(float) - 0.3).abs()
        use_rt_col = "reverberation" if "reverberation" in tmp.columns else (
            "RT60" if "RT60" in tmp.columns else "rt60"
        )
        X = tmp[["frequency", use_rt_col, "RT60_deviation"]].astype(float).values
        log_fn("→ Using engineered features: ['frequency','(rt60)','RT60_deviation']")
        return model.predict(X)

    raise RuntimeError("No compatible feature layout for the loaded model.")


# ----------------- GUI -----------------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")

        self.title("Project Design T6 - Build 1.0 7")
        self.geometry("1024x600")
        self.minsize(1000, 580)
        self.configure(fg_color=BG)

        self.bind("<F11>", lambda e: self.attributes("-fullscreen", True))
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))

        self.proc = None
        self.proc_thread = None
        self.stop_requested = False

        self._build_ui()

    # ---------- Gradient helpers ----------
    @staticmethod
    def _hex_to_rgb(h: str):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @staticmethod
    def _rgb_to_hex(rgb):
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    @staticmethod
    def _lerp(a, b, t: float):
        return int(a + (b - a) * t)

    def _gradient_color_at(self, stops, t: float):
        if t <= stops[0][0]:
            return stops[0][1]
        if t >= stops[-1][0]:
            return stops[-1][1]

        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                local = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                r0, g0, b0 = self._hex_to_rgb(c0)
                r1, g1, b1 = self._hex_to_rgb(c1)
                return self._rgb_to_hex((
                    self._lerp(r0, r1, local),
                    self._lerp(g0, g1, local),
                    self._lerp(b0, b1, local),
                ))
        return stops[-1][1]

    def _draw_horizontal_gradient(self, canvas: tk.Canvas, stops):
        canvas.delete("grad")
        w = max(1, canvas.winfo_width())
        h = max(1, canvas.winfo_height())
        for x in range(w):
            t = x / float(w - 1) if w > 1 else 0.0
            color = self._gradient_color_at(stops, t)
            canvas.create_line(x, 0, x, h, fill=color, tags=("grad",))

    # ---------- Popup helper (supports optional link button) ----------
    def _popup(self, title: str, message: str, link_url: str | None = None, link_label: str = "Open Link"):
        def _show():
            win = ctk.CTkToplevel(self)
            win.title(title)
            win.configure(fg_color=PANEL)
            win.attributes("-topmost", True)

            w, h = 540, 290
            try:
                x = self.winfo_rootx() + (self.winfo_width() // 2) - (w // 2)
                y = self.winfo_rooty() + (self.winfo_height() // 2) - (h // 2)
                win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
            except Exception:
                win.geometry(f"{w}x{h}")

            frame = ctk.CTkFrame(win, fg_color=PANEL_2, corner_radius=RADIUS)
            frame.pack(fill="both", expand=True, padx=14, pady=14)

            # Gradient strip (Instagram-like)
            grad = tk.Canvas(frame, height=8, highlightthickness=0, bd=0, bg=PANEL_2)
            grad.pack(fill="x", padx=12, pady=(12, 8))
            stops = [
                (0.00, IG_PURPLE),
                (0.45, IG_PINK),
                (0.75, IG_ORANGE),
                (1.00, IG_YELLOW),
            ]
            grad.bind("<Configure>", lambda e: self._draw_horizontal_gradient(grad, stops))

            ctk.CTkLabel(
                frame,
                text=title,
                text_color=TEXT,
                font=("Segoe UI Semibold", 18),
            ).pack(anchor="w", padx=14, pady=(0, 6))

            box = ctk.CTkTextbox(
                frame,
                height=120,
                fg_color=FIELD,
                text_color=TEXT,
                corner_radius=RADIUS,
                wrap="word",
                font=("Segoe UI", 13),
            )
            box.pack(fill="both", expand=True, padx=14, pady=(0, 12))
            box.insert("1.0", message)
            box.configure(state="disabled")

            btn_row = ctk.CTkFrame(frame, fg_color="transparent")
            btn_row.pack(fill="x", padx=14, pady=(0, 14))

            def _open_link():
                try:
                    if link_url:
                        webbrowser.open(link_url)
                except Exception:
                    pass
                win.destroy()

            close_btn = ctk.CTkButton(
                btn_row,
                text="Close",
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
                height=44,
                corner_radius=RADIUS,
                font=("Segoe UI Semibold", 14),
                command=win.destroy,
            )
            close_btn.pack(side="left", fill="x", expand=True)

            if link_url:
                link_btn = ctk.CTkButton(
                    btn_row,
                    text=link_label,
                    fg_color=IG_PURPLE,
                    hover_color=IG_PINK,
                    height=44,
                    corner_radius=RADIUS,
                    font=("Segoe UI Semibold", 14),
                    command=_open_link,
                )
                link_btn.pack(side="left", fill="x", expand=True, padx=(10, 0))

            try:
                win.grab_set()
            except Exception:
                pass

        self.after(0, _show)

    # Smooth scrolling on Windows touch / wheel
    def _enable_touch_scroll(self, scrollframe: ctk.CTkScrollableFrame):
        def _on_mousewheel(event):
            try:
                scrollframe._parent_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        def _on_linux_up(_event):
            try:
                scrollframe._parent_canvas.yview_scroll(-1, "units")
            except Exception:
                pass

        def _on_linux_down(_event):
            try:
                scrollframe._parent_canvas.yview_scroll(1, "units")
            except Exception:
                pass

        self.bind_all("<MouseWheel>", _on_mousewheel)
        self.bind_all("<Button-4>", _on_linux_up)
        self.bind_all("<Button-5>", _on_linux_down)

    def _build_ui(self):
        # Header
        top = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=RADIUS)
        top.pack(fill="x", padx=PAD_X, pady=(PAD_Y, 8))

        header_stops = [
            (0.00, IG_PURPLE),
            (0.45, IG_PINK),
            (0.75, IG_ORANGE),
            (1.00, IG_YELLOW),
        ]

        grad_canvas = tk.Canvas(top, height=10, highlightthickness=0, bd=0, bg=PANEL)
        grad_canvas.pack(fill="x", padx=12, pady=(10, 6))
        grad_canvas.bind("<Configure>", lambda e: self._draw_horizontal_gradient(grad_canvas, header_stops))

        title_row = ctk.CTkFrame(top, fg_color="transparent")
        title_row.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkLabel(
            title_row,
            text="Project Design T6 • Build 1.0 7",
            font=("Segoe UI Semibold", FONT_TITLE),
            text_color=TEXT,
        ).pack(side="left")

        ctk.CTkLabel(
            title_row,
            text="dgpo5 • Touch UI • 1°/s or 5°/s",
            font=("Segoe UI", FONT_SUB),
            text_color=TEXT_DIM,
        ).pack(side="left", padx=10)

        # Main area
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=PAD_X, pady=(0, PAD_Y))

        # LEFT: scrollable capture column
        left_container = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=RADIUS, width=430)
        left_container.pack(side="left", fill="y", padx=(0, 10))
        left_container.pack_propagate(False)

        left = ctk.CTkScrollableFrame(
            left_container,
            fg_color="transparent",
            corner_radius=RADIUS,
            scrollbar_button_color=IG_PURPLE,
            scrollbar_button_hover_color=IG_PINK,
        )
        left.pack(fill="both", expand=True, padx=6, pady=6)
        self._enable_touch_scroll(left)

        # RIGHT: log
        right = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=RADIUS)
        right.pack(side="left", fill="both", expand=True)

        # --- CAPTURE SECTION ---
        ctk.CTkLabel(left, text="Capture", text_color=TEXT, font=("Segoe UI Semibold", 16)).pack(
            anchor="w", padx=14, pady=(12, 6)
        )

        # Port row
        port_row = ctk.CTkFrame(left, fg_color="transparent")
        port_row.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkLabel(port_row, text="Port", text_color=TEXT_DIM, font=("Segoe UI", FONT_LABEL)).pack(side="left", padx=(0, 8))
        default_port = "COM5" if platform.system() == "Windows" else "/dev/ttyUSB0"
        self.port_var = ctk.StringVar(value=default_port)

        self.port_combo = ctk.CTkComboBox(
            port_row,
            values=self._scan_ports(),
            variable=self.port_var,
            width=220,
            height=ENTRY_H,
            fg_color=FIELD,
            border_color=IG_PURPLE,
            button_color=IG_PURPLE,
            button_hover_color=IG_PINK,
            text_color=TEXT,
            corner_radius=12,
        )
        self.port_combo.pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            port_row,
            text="Refresh",
            width=120,
            height=ENTRY_H,
            command=self._refresh_ports,
            fg_color=IG_PURPLE,
            hover_color=IG_PINK,
            corner_radius=12,
            font=("Segoe UI Semibold", 13),
        ).pack(side="left")

        # Rows + speed
        rs_row = ctk.CTkFrame(left, fg_color="transparent")
        rs_row.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkLabel(rs_row, text=f"Rows (≤ {MAX_ROWS})", text_color=TEXT_DIM, font=("Segoe UI", FONT_LABEL)).pack(side="left", padx=(0, 8))
        self.count_var = ctk.IntVar(value=MAX_ROWS)
        ctk.CTkEntry(
            rs_row,
            width=90,
            height=ENTRY_H,
            textvariable=self.count_var,
            fg_color=FIELD,
            border_color=IG_PURPLE,
            corner_radius=12,
            font=("Segoe UI", 14),
        ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(rs_row, text="Speed", text_color=TEXT_DIM, font=("Segoe UI", FONT_LABEL)).pack(side="left", padx=(0, 8))
        self.speed_var = ctk.StringVar(value="5")
        self.speed_segment = ctk.CTkSegmentedButton(
            rs_row,
            values=["1", "5"],
            variable=self.speed_var,
            width=160,
            height=ENTRY_H,
            fg_color=FIELD,
            selected_color=IG_PINK,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=PANEL_2,
            unselected_hover_color="#23253a",
            text_color=(TEXT, TEXT),
            corner_radius=12,
            font=("Segoe UI Semibold", 13),
        )
        self.speed_segment.pack(side="left")

        # Toggles
        toggles = ctk.CTkFrame(left, fg_color="transparent")
        toggles.pack(fill="x", padx=14, pady=(0, 10))

        self.sim_var = ctk.BooleanVar(value=False)
        self.skip_var = ctk.BooleanVar(value=False)

        ctk.CTkCheckBox(
            toggles,
            text="Simulate (no serial)",
            variable=self.sim_var,
            fg_color=IG_PINK,
            border_color=IG_PINK,
            corner_radius=10,
            font=("Segoe UI", 13),
            checkbox_width=26,
            checkbox_height=26,
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkCheckBox(
            toggles,
            text="Skip Google Sheets upload",
            variable=self.skip_var,
            fg_color=IG_PINK,
            border_color=IG_PINK,
            corner_radius=10,
            font=("Segoe UI", 13),
            checkbox_width=26,
            checkbox_height=26,
        ).pack(anchor="w")

        # --- ADVANCED SECTION ---
        ctk.CTkLabel(left, text="Advanced", text_color=TEXT, font=("Segoe UI Semibold", 16)).pack(
            anchor="w", padx=14, pady=(10, 6)
        )

        adv = ctk.CTkFrame(left, fg_color=PANEL_2, corner_radius=RADIUS)
        adv.pack(fill="x", padx=14, pady=(0, 10))

        def add_field(parent, row_i: int, label: str, var: ctk.StringVar, width: int = 300):
            ctk.CTkLabel(parent, text=label, text_color=TEXT_DIM, font=("Segoe UI", FONT_LABEL)).grid(
                row=row_i, column=0, sticky="w", padx=(12, 10), pady=(10, 6)
            )
            ent = ctk.CTkEntry(
                parent,
                textvariable=var,
                width=width,
                height=ENTRY_H,
                fg_color=FIELD,
                border_color=IG_PURPLE,
                corner_radius=12,
                font=("Segoe UI", 13),
            )
            ent.grid(row=row_i, column=1, sticky="we", padx=(0, 12), pady=(10, 6))
            return ent

        self.sheet_var = ctk.StringVar(
            value="https://docs.google.com/spreadsheets/d/1OAfQI6MwheL6wIes1EhGjak3G1jSVLFGppmzqTL9MWQ/edit?usp=sharing"
        )
        add_field(adv, 0, "Sheet Link", self.sheet_var, width=300)

        default_json = os.path.join(os.path.dirname(SCRIPT_PATH), "projectdesignt6-b8c2872f2067.json")
        self.json_var = ctk.StringVar(value=default_json)
        add_field(adv, 1, "Service JSON", self.json_var, width=300)

        self.model_var = ctk.StringVar(value=os.path.join(os.path.dirname(SCRIPT_PATH), "reverb_zone_rf.joblib"))
        add_field(adv, 2, "Model Path (.joblib)", self.model_var, width=300)

        ctk.CTkLabel(adv, text="Layer (Sheet)", text_color=TEXT_DIM, font=("Segoe UI", FONT_LABEL)).grid(
            row=3, column=0, sticky="w", padx=(12, 10), pady=(10, 6)
        )
        self.layer_var = ctk.StringVar(value="1")
        self.layer_menu = ctk.CTkOptionMenu(
            adv,
            values=["1", "2", "3", "4"],
            variable=self.layer_var,
            width=160,
            height=ENTRY_H,
            fg_color=FIELD,
            button_color=IG_PURPLE,
            button_hover_color=IG_PINK,
            text_color=TEXT,
            corner_radius=12,
            font=("Segoe UI Semibold", 13),
        )
        self.layer_menu.grid(row=3, column=1, sticky="w", padx=(0, 12), pady=(10, 6))

        ctk.CTkLabel(adv, text="Ceiling Offset (cm)", text_color=TEXT_DIM, font=("Segoe UI", FONT_LABEL)).grid(
            row=4, column=0, sticky="w", padx=(12, 10), pady=(10, 6)
        )
        self.ceiling_offset_var = ctk.StringVar(value=str(DEFAULT_CEILING_OFFSET))
        ctk.CTkEntry(
            adv,
            textvariable=self.ceiling_offset_var,
            width=160,
            height=ENTRY_H,
            fg_color=FIELD,
            border_color=IG_PURPLE,
            corner_radius=12,
            font=("Segoe UI", 13),
        ).grid(row=4, column=1, sticky="w", padx=(0, 12), pady=(10, 6))

        self.apply_ceiling_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            adv,
            text="Apply ceiling offset in Python (dgpo5)",
            variable=self.apply_ceiling_var,
            fg_color=IG_PINK,
            border_color=IG_PINK,
            corner_radius=10,
            font=("Segoe UI", 13),
            checkbox_width=26,
            checkbox_height=26,
        ).grid(row=5, column=1, sticky="w", padx=(0, 12), pady=(0, 10))

        adv.grid_columnconfigure(1, weight=1)

        # --- ACTION BUTTONS ---
        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(0, 14))

        self.start_btn = ctk.CTkButton(
            actions,
            text="Start Capture",
            command=self.start,
            fg_color=IG_PURPLE,
            hover_color=IG_PINK,
            corner_radius=RADIUS,
            height=BTN_H,
            font=("Segoe UI Semibold", 16),
        )
        self.start_btn.pack(fill="x", pady=(0, 10))

        self.stop_btn = ctk.CTkButton(
            actions,
            text="Stop",
            command=self.stop,
            fg_color="#2a2c3a",
            hover_color="#34364a",
            corner_radius=RADIUS,
            height=BTN_H_SMALL,
            font=("Segoe UI Semibold", 15),
        )
        self.stop_btn.pack(fill="x", pady=(0, 10))

        self.deploy_btn = ctk.CTkButton(
            actions,
            text="Deploy Class to Sheet",
            command=self.deploy_to_gsheet,
            fg_color=IG_PINK,
            hover_color=ACCENT_HOVER,
            corner_radius=RADIUS,
            height=BTN_H,
            font=("Segoe UI Semibold", 16),
        )
        self.deploy_btn.pack(fill="x")

        # --- RIGHT LOG ---
        right_top = ctk.CTkFrame(right, fg_color="transparent")
        right_top.pack(fill="x", padx=12, pady=(12, 6))

        log_strip = tk.Canvas(right_top, height=6, highlightthickness=0, bd=0, bg=PANEL)
        log_strip.pack(fill="x", side="top", pady=(0, 6))
        log_strip.bind("<Configure>", lambda e: self._draw_horizontal_gradient(log_strip, header_stops))

        row = ctk.CTkFrame(right_top, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkLabel(row, text="Live Log", text_color=TEXT, font=("Segoe UI Semibold", 16)).pack(side="left")

        self.progress = ctk.CTkProgressBar(
            row, width=260, progress_color=IG_PINK, fg_color="#1b1b23"
        )
        self.progress.set(0)
        self.progress.pack(side="right", padx=(10, 0), pady=6)

        self.log = ctk.CTkTextbox(
            right,
            fg_color="#0f1017",
            text_color=TEXT,
            corner_radius=RADIUS,
            wrap="word",
            font=("Consolas", FONT_MONO),
        )
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._write("Ready — dgpo5 backend. Instagram-soft theme + gradient + popups + deploy link enabled.")

    # ----------------- Helpers -----------------
    def _write(self, text: str):
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.update_idletasks()

    def _scan_ports(self):
        ports = []
        if list_ports:
            try:
                for p in list_ports.comports():
                    ports.append(p.device)
            except Exception:
                pass
        return ports or (["COM3", "COM5"] if platform.system() == "Windows" else ["/dev/ttyUSB0", "/dev/ttyACM0"])

    def _refresh_ports(self):
        self.port_combo.configure(values=self._scan_ports())

    # ----------------- Capture start/stop -----------------
    def start(self):
        if self.proc and self.proc.poll() is None:
            self._write("⚠️ Already running.")
            return

        try:
            count = int(self.count_var.get() or MAX_ROWS)
        except Exception:
            count = MAX_ROWS
        count = max(1, min(count, MAX_ROWS))

        try:
            speed = int(self.speed_var.get())
        except Exception:
            speed = 5
        if speed not in (1, 5):
            speed = 5

        interval = float(ANGLE_STEP) / float(speed)
        port = (self.port_var.get() or "").strip()

        cmd = [
            sys.executable, SCRIPT_PATH,
            "--port", port,
            "--count", str(count),
            "--angle-step", f"{float(ANGLE_STEP):.3f}",
            "--angle-speed", str(speed),
            "--interval", f"{interval:.3f}",
            "--sheet-link", self.sheet_var.get().strip(),
            "--service-json", self.json_var.get().strip(),
            "--model-path", self.model_var.get().strip(),
            "--no-predict",
        ]

        if self.apply_ceiling_var.get():
            try:
                off = float(self.ceiling_offset_var.get())
            except Exception:
                off = DEFAULT_CEILING_OFFSET
            cmd.extend(["--apply-ceiling-offset", "--ceiling-offset", str(off)])

        if self.sim_var.get():
            cmd.append("--simulate")
        if self.skip_var.get():
            cmd.append("--skip-gsheets")

        try:
            sheet_index = max(0, int(self.layer_var.get()) - 1)
        except Exception:
            sheet_index = 0
        cmd.extend(["--sheet-index", str(sheet_index)])

        self._write(f"▶ Running:\n{' '.join(cmd)}")

        self.stop_requested = False
        self.progress.set(0)

        def run():
            saved_ok = False
            uploaded_ok = False
            upload_failed = False
            saved_path = ""
            last_error_line = ""

            def _inspect(line: str):
                nonlocal saved_ok, uploaded_ok, upload_failed, saved_path, last_error_line
                l = line.strip()

                if "[OK] Saved" in l and "->" in l:
                    saved_ok = True
                    try:
                        saved_path = l.split("->", 1)[1].strip()
                    except Exception:
                        pass

                if "[OK] Successfully uploaded" in l or "Successfully uploaded" in l:
                    uploaded_ok = True

                if "Google Sheets upload failed" in l or "[ERROR] Google Sheets upload failed" in l:
                    upload_failed = True
                    last_error_line = l

                if "[ERROR]" in l or "Traceback" in l:
                    last_error_line = l

            try:
                self.proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                start_t = time.time()
                for line in self.proc.stdout:
                    if self.stop_requested:
                        try:
                            self.proc.terminate()
                        except Exception:
                            pass
                        break

                    self._write(line.rstrip("\n"))
                    _inspect(line)
                    self.progress.set((time.time() - start_t) % 1.0)

                rc = self.proc.wait()

                if self.stop_requested:
                    self._write("⏹ Stopped by user.")
                    self._popup("Capture stopped", "Capture was stopped by the user.")
                else:
                    self._write("✅ Done!")

                    if rc == 0 and saved_ok and (self.skip_var.get() or uploaded_ok) and not upload_failed:
                        msg = "Capture completed successfully.\n\n"
                        if saved_path:
                            msg += f"CSV saved:\n{saved_path}\n\n"
                        msg += "Upload: SUCCESS" if not self.skip_var.get() else "Upload: SKIPPED (by user)"
                        self._popup("Capture complete", msg)
                    else:
                        msg = "Capture finished, but upload may have FAILED.\n\n"
                        if saved_path:
                            msg += f"CSV saved:\n{saved_path}\n\n"
                        if self.skip_var.get():
                            msg += "Upload: SKIPPED (by user)\n"
                        else:
                            msg += "Upload: FAILED\n"
                        if last_error_line:
                            msg += f"\nLast error:\n{last_error_line}"
                        self._popup("Upload complete", msg)

            except FileNotFoundError:
                self._write(f"❌ dgpo5.py not found!\nPath: {SCRIPT_PATH}")
                self._popup("Upload complete", f"Backend script not found:\n{SCRIPT_PATH}")
            except Exception as e:
                self._write(f"❌ Error: {e}")
                self._popup("Upload complete", f"Unexpected error:\n{e}")
            finally:
                self.progress.set(0)
                self.proc = None

        self.proc_thread = threading.Thread(target=run, daemon=True)
        self.proc_thread.start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.stop_requested = True
            self._write("…Stopping process…")
        else:
            self._write("ℹ️ Nothing is running.")

    # ----------------- Deploy classification to Google Sheet -----------------
    def deploy_to_gsheet(self):
        if gspread is None or Credentials is None or pd is None:
            self._write("❌ Missing packages. Install:\n  pip install gspread google-auth pandas joblib")
            self._popup("Upload complete", "Missing packages. Install:\n- gspread\n- google-auth\n- pandas\n- joblib")
            return

        sheet_url = self.sheet_var.get().strip()
        json_path = self.json_var.get().strip()
        model_path = self.model_var.get().strip()

        if not sheet_url:
            self._write("❌ Provide a Google Sheet URL.")
            self._popup("Upload complete", "Missing Google Sheet URL.")
            return
        if not json_path or not os.path.isfile(json_path):
            self._write("❌ Service Account JSON not found at the given path.")
            self._popup("Upload complete", f"Service JSON not found:\n{json_path}")
            return

        self._write("🚀 Deploying classification to Google Sheet...")
        self.deploy_btn.configure(state="disabled")
        self.update_idletasks()

        def worker():
            try:
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                creds = Credentials.from_service_account_file(json_path, scopes=scopes)
                client = gspread.authorize(creds)

                m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
                if not m:
                    raise ValueError("Invalid Google Sheet URL")
                sheet_id = m.group(1)

                sh = client.open_by_key(sheet_id)

                try:
                    sheet_index = max(0, int(self.layer_var.get()) - 1) if hasattr(self, "layer_var") else 0
                except Exception:
                    sheet_index = 0

                sheets = sh.worksheets()
                if sheet_index < 0:
                    sheet_index = 0
                if sheet_index >= len(sheets):
                    for i in range(len(sheets), sheet_index + 1):
                        sh.add_worksheet(title=f"Sheet{i+1}", rows=2000, cols=30)
                    sheets = sh.worksheets()

                ws = sh.get_worksheet(sheet_index)
                self._write(f"→ Using worksheet index {sheet_index} (title='{ws.title}')")

                self._write("→ Downloading sheet...")
                rows = ws.get_all_records()
                if not rows:
                    raise ValueError("Sheet is empty.")

                df_raw = pd.DataFrame(rows)

                dgpo_cols = ["sensor", "angle", "rt60", "utv", "utvh", "dB", "class"]
                canonical_cols = ["angle", "reverberation", "ultrasonicValue", "db", "Classification"]

                has_dgpo = any(c in df_raw.columns for c in ("sensor", "rt60", "utv", "utvh", "dB", "class"))

                df = df_raw.copy()
                df_work = df_raw.copy()

                if "angle" not in df_work.columns:
                    for alt in ("number", "Angle", "id", "ID"):
                        if alt in df_work.columns:
                            df_work["angle"] = df_work[alt]
                            break

                if "reverberation" not in df_work.columns:
                    if "rt60" in df_work.columns:
                        df_work["reverberation"] = df_work["rt60"]
                    else:
                        for alt in ("RT60", "Reverberation", "Rt60"):
                            if alt in df_work.columns:
                                df_work["reverberation"] = df_work[alt]
                                break

                if "ultrasonicValue" not in df_work.columns:
                    if "utv" in df_work.columns:
                        df_work["ultrasonicValue"] = df_work["utv"]
                    else:
                        for alt in ("Ultrasonic Value", "Ultrasonic", "ultrasonic"):
                            if alt in df_work.columns:
                                df_work["ultrasonicValue"] = df_work[alt]
                                break

                if "db" not in df_work.columns:
                    if "dB" in df_work.columns:
                        df_work["db"] = df_work["dB"]
                    else:
                        for alt in ("DB", "decibel"):
                            if alt in df_work.columns:
                                df_work["db"] = df_work[alt]
                                break

                for col in ("angle", "reverberation", "ultrasonicValue", "db"):
                    if col not in df_work.columns:
                        df_work[col] = ""

                if model_path and os.path.isfile(model_path) and joblib is not None:
                    try:
                        model = _load_model_any(model_path)
                        preds = _predict_with_model(model, df_work, self._write)
                        df_work["Classification"] = list(preds)
                        self._write(f"✅ Classified with model: {os.path.basename(model_path)}")
                    except Exception as e:
                        self._write(f"⚠️ Model-based classification failed: {e}")
                        self._write("→ Falling back to rule-based labels.")
                        df_work["Classification"] = df_work["reverberation"].apply(classify_rt60_rule)
                else:
                    self._write("→ No model found, using RT60 rule-based labels.")
                    df_work["Classification"] = df_work["reverberation"].apply(classify_rt60_rule)

                df["Classification"] = df_work["Classification"].values

                if "class" in df.columns:
                    df["class"] = df["Classification"]
                elif has_dgpo:
                    df["class"] = df["Classification"]

                if has_dgpo:
                    if "sensor" not in df.columns:
                        df["sensor"] = ""
                    if "angle" not in df.columns:
                        df["angle"] = df_work["angle"]
                    if "rt60" not in df.columns:
                        df["rt60"] = df_work["reverberation"] if "reverberation" in df_work.columns else ""
                    if "utv" not in df.columns:
                        df["utv"] = df_work["ultrasonicValue"] if "ultrasonicValue" in df_work.columns else ""
                    if "utvh" not in df.columns:
                        df["utvh"] = ""
                    if "dB" not in df.columns:
                        df["dB"] = df_work["db"] if "db" in df_work.columns else ""
                    out_cols = dgpo_cols
                    self._write("→ Uploading updated sheet (dgpo schema)…")
                else:
                    for c in canonical_cols:
                        if c not in df_work.columns:
                            df_work[c] = ""
                    out_cols = canonical_cols
                    df = df_work
                    self._write("→ Uploading updated sheet (canonical schema)…")

                out_df = df[out_cols].copy()
                ws.clear()
                ws.update([out_cols] + out_df.astype(object).values.tolist())

                self._write("✅ Deploy complete. Sheet updated.")
                self._popup(
                    "Deploy complete",
                    f"Deploy finished successfully.\n\nWorksheet: {ws.title}\n\nOpen simulation page?",
                    link_url=SIMULATION_URL,
                    link_label="Go to Simulation",
                )
            except Exception as e:
                self._write(f"❌ Deploy error: {e}")
                self._popup("Upload complete", f"Deploy FAILED.\n\nError:\n{e}")
            finally:
                self.deploy_btn.configure(state="normal")

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()