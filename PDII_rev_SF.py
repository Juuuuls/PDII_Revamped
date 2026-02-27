# reverb_gui_ctk_7inch.py
# Project Design T6 - Build 1.0 7
# Optimized for Waveshare 7" (1024x600). Sleek black + hot pink.
#
# UPDATED FOR dgpo4.py (angle_step is float; removed --max-angle)
# 2 sensors opposite (180° apart):
# - 1.8° resolution -> 100 servo stops -> 200 rows (2 sensors per stop)
#
# Start/Stop live capture (calls dgpo4.py)
# Deploy Class to Sheet:
#   - Uses your .joblib model if provided (auto-matches feature names)
#   - Falls back to RT60 rule (based on 'reverberation') if model not available
#
# Deploy sheet schema (on upload): angle, reverberation, ultrasonicValue, db, Classification
# Accepts dgpo3 columns too: sensor, angle, rt60, utv, utvh, dB, class

import os
import sys
import subprocess
import threading
import time
import platform
import re

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


# Path to backend script (same folder)
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dgpo4.py")

# ✅ NEW scanning assumptions
# 360 / 1.8 = 200 unique directions; with 2 opposite sensors => 200 rows
ANGLE_STEP = 1.8
MAX_ROWS = 200

# Theme
PINK = "#ff4dc4"
PINK_HOVER = "#ff73d9"
DARK = "#0b0b0e"
DARK2 = "#141419"
TEXT_DIM = "#cfcfe0"


# ----------------- Classification helpers -----------------
def classify_rt60_rule(rt: float) -> str:
    """Rule-based classifier using RT60 only (here column name is 'reverberation')."""
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
    """Load joblib model; accept either dict bundle {'model': ...} or raw estimator."""
    if joblib is None:
        raise RuntimeError("joblib not installed")
    obj = joblib.load(path)
    if isinstance(obj, dict) and "model" in obj:
        return obj["model"]
    return obj


def _ensure_canonical_columns(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Normalize incoming sheet columns to canonical schema:
        angle, reverberation, ultrasonicValue, db, Classification

    Accepts dgpo3 columns:
        sensor, angle, rt60, utv, utvh, dB, class
    """
    work = df.copy()

    # angle
    if "angle" not in work.columns:
        for alt in ("number", "Angle", "id", "ID"):
            if alt in work.columns:
                work.rename(columns={alt: "angle"}, inplace=True)
                break

    # reverberation (rt60)
    if "reverberation" not in work.columns:
        for alt in ("rt60", "RT60", "Reverberation", "Rt60"):
            if alt in work.columns:
                work.rename(columns={alt: "reverberation"}, inplace=True)
                break

    # ultrasonicValue (utv)
    if "ultrasonicValue" not in work.columns:
        for alt in ("utv", "Ultrasonic Value", "Ultrasonic", "ultrasonic"):
            if alt in work.columns:
                work.rename(columns={alt: "ultrasonicValue"}, inplace=True)
                break

    # db (dB)
    if "db" not in work.columns:
        for alt in ("dB", "DB", "decibel"):
            if alt in work.columns:
                work.rename(columns={alt: "db"}, inplace=True)
                break

    # Classification (class)
    if "Classification" not in work.columns:
        for alt in ("class", "Class", "classification", "CLASS"):
            if alt in work.columns:
                work.rename(columns={alt: "Classification"}, inplace=True)
                break

    return work


def _predict_with_model(model, df: "pd.DataFrame", log_fn):
    """
    Use model.feature_names_in_ when available; else try sensible fallbacks.
    Canonical dataframe has: angle, reverberation, ultrasonicValue, db, Classification
    We may synthesize:
      - RT60_deviation = |reverberation - 0.3|
      - frequency (default 1000.0) if model expects it
    """
    work = _ensure_canonical_columns(df)
    want = list(getattr(model, "feature_names_in_", []))

    # Provide compatibility names if needed
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

    # Preferred: use model's exact features
    if want:
        try:
            X = work[want].astype(float)
            log_fn(f"→ Using model features: {want}")
            return model.predict(X)
        except Exception as e:
            log_fn(f"…could not use feature_names_in_ {want}: {e}")

    # Fallbacks
    if "reverberation" in work.columns:
        try:
            X = work[["reverberation"]].astype(float).values
            log_fn("→ Using features: ['reverberation']")
            return model.predict(X)
        except Exception as e:
            log_fn(f"…shape ['reverberation'] failed: {e}")

    if all(c in work.columns for c in ["ultrasonicValue", "reverberation"]):
        try:
            X = work[["ultrasonicValue", "reverberation"]].astype(float).values
            log_fn("→ Using features: ['ultrasonicValue','reverberation']")
            return model.predict(X)
        except Exception as e:
            log_fn(f"…shape ['ultrasonicValue','reverberation'] failed: {e}")

    base = None
    if "reverberation" in work.columns:
        base = work["reverberation"]
    elif "RT60" in work.columns:
        base = work["RT60"]
    elif "rt60" in work.columns:
        base = work["rt60"]

    if base is not None:
        try:
            tmp = work.copy()
            if "frequency" not in tmp.columns:
                tmp["frequency"] = 1000.0
            if "RT60_deviation" not in tmp.columns:
                tmp["RT60_deviation"] = (base.astype(float) - 0.3).abs()
            use_rt_col = "reverberation" if "reverberation" in tmp.columns else ("RT60" if "RT60" in tmp.columns else "rt60")
            X = tmp[["frequency", use_rt_col, "RT60_deviation"]].astype(float).values
            log_fn("→ Using engineered features: ['frequency','(rt60)','RT60_deviation']")
            return model.predict(X)
        except Exception as e:
            log_fn(f"…engineered shape failed: {e}")

    raise RuntimeError("No compatible feature layout for the loaded model.")


# ----------------- GUI -----------------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        self.title("Project Design T6 - Build 1.0 7")
        self.geometry("1024x600")
        self.minsize(1000, 580)
        self.configure(fg_color=DARK)

        self.bind("<F11>", lambda e: self.attributes("-fullscreen", True))
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))

        self.proc = None
        self.proc_thread = None
        self.stop_requested = False

        self._build_ui()

    # ----------------- UI -----------------
    def _build_ui(self):
        # HEADER
        top = ctk.CTkFrame(self, fg_color=DARK2, corner_radius=12)
        top.pack(fill="x", padx=10, pady=(10, 6))

        title = ctk.CTkLabel(
            top, text="🎧 Project Design T6 - Build 1.0 7",
            font=("Segoe UI Semibold", 18), text_color=PINK
        )
        title.pack(side="left", padx=10, pady=6)

        subtitle = ctk.CTkLabel(
            top, text="dgpo4 • Live Capture • 1°/s or 5°/s",
            font=("Segoe UI", 12), text_color=TEXT_DIM
        )
        subtitle.pack(side="left", padx=8)

        # BODY
        body = ctk.CTkFrame(self, fg_color=DARK2, corner_radius=12)
        body.pack(fill="x", padx=10, pady=(0, 6))

        # Row 1: Port + Rows + Speed + Toggles
        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(8, 4))

        ctk.CTkLabel(row1, text="Port", text_color=TEXT_DIM, font=("Segoe UI", 12)).pack(side="left", padx=(0, 6))
        default_port = "COM5" if platform.system() == "Windows" else "/dev/ttyUSB0"
        self.port_var = ctk.StringVar(value=default_port)
        self.port_combo = ctk.CTkComboBox(
            row1,
            values=self._scan_ports(),
            variable=self.port_var,
            width=220,
            fg_color="#181820",
            border_color=PINK,
            button_color=PINK,
            text_color="white",
            corner_radius=8
        )
        self.port_combo.pack(side="left")
        ctk.CTkButton(
            row1, text="↻", width=36, command=self._refresh_ports,
            fg_color=PINK, hover_color=PINK_HOVER, corner_radius=8
        ).pack(side="left", padx=6)

        ctk.CTkLabel(row1, text=f"Rows (≤ {MAX_ROWS})", text_color=TEXT_DIM, font=("Segoe UI", 12)).pack(side="left", padx=(12, 6))
        self.count_var = ctk.IntVar(value=MAX_ROWS)
        ctk.CTkEntry(
            row1, width=80, textvariable=self.count_var,
            fg_color="#181820", border_color=PINK, corner_radius=8
        ).pack(side="left")

        ctk.CTkLabel(row1, text="Angle Speed (°/s)", text_color=TEXT_DIM, font=("Segoe UI", 12)).pack(side="left", padx=(12, 6))
        self.speed_var = ctk.StringVar(value="5")
        self.speed_segment = ctk.CTkSegmentedButton(
            row1, values=["1", "5"], variable=self.speed_var,
            fg_color="#181820",
            selected_color=PINK, selected_hover_color=PINK_HOVER,
            unselected_color="#23232b", unselected_hover_color="#2c2c36",
            text_color=("white", "white"),
            corner_radius=8
        )
        self.speed_segment.pack(side="left")

        # Toggles
        row1b = ctk.CTkFrame(body, fg_color="transparent")
        row1b.pack(fill="x", padx=10, pady=(0, 8))

        self.sim_var = ctk.BooleanVar(value=False)
        self.skip_var = ctk.BooleanVar(value=False)

        ctk.CTkCheckBox(
            row1b, text="Simulate (no serial)",
            variable=self.sim_var, fg_color=PINK, border_color=PINK,
            corner_radius=8, font=("Segoe UI", 12)
        ).pack(side="left", padx=(0, 12))
        ctk.CTkCheckBox(
            row1b, text="Skip Google Sheets upload",
            variable=self.skip_var, fg_color=PINK, border_color=PINK,
            corner_radius=8, font=("Segoe UI", 12)
        ).pack(side="left")

        # Advanced Panel
        adv = ctk.CTkFrame(body, fg_color="#121217", corner_radius=10)
        adv.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(adv, text="Advanced", text_color=PINK, font=("Segoe UI Semibold", 13)).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 6)
        )

        # Sheet link
        ctk.CTkLabel(adv, text="Sheet Link", text_color=TEXT_DIM, font=("Segoe UI", 12)).grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self.sheet_var = ctk.StringVar(
            value="https://docs.google.com/spreadsheets/d/1OAfQI6MwheL6wIes1EhGjak3G1jSVLFGppmzqTL9MWQ/edit?usp=sharing"
        )
        ctk.CTkEntry(
            adv, textvariable=self.sheet_var, width=640,
            fg_color="#181820", border_color=PINK, corner_radius=8
        ).grid(row=1, column=1, sticky="we", padx=8, pady=4)

        # Service JSON
        ctk.CTkLabel(adv, text="Service JSON", text_color=TEXT_DIM, font=("Segoe UI", 12)).grid(row=2, column=0, sticky="w", padx=10, pady=4)
        default_json = os.path.join(os.path.dirname(SCRIPT_PATH), "projectdesignt6-b8c2872f2067.json")
        self.json_var = ctk.StringVar(value=default_json)
        ctk.CTkEntry(
            adv, textvariable=self.json_var, width=640,
            fg_color="#181820", border_color=PINK, corner_radius=8
        ).grid(row=2, column=1, sticky="we", padx=8, pady=4)

        # Model Path
        ctk.CTkLabel(adv, text="Model Path (.joblib)", text_color=TEXT_DIM, font=("Segoe UI", 12)).grid(row=3, column=0, sticky="w", padx=10, pady=4)
        self.model_var = ctk.StringVar(value=os.path.join(os.path.dirname(SCRIPT_PATH), "reverb_zone_rf.joblib"))
        ctk.CTkEntry(
            adv, textvariable=self.model_var, width=640,
            fg_color="#181820", border_color=PINK, corner_radius=8
        ).grid(row=3, column=1, sticky="we", padx=8, pady=4)

        # Layer selector
        ctk.CTkLabel(adv, text="Layer (Sheet)", text_color=TEXT_DIM, font=("Segoe UI", 12)).grid(row=4, column=0, sticky="w", padx=10, pady=4)
        self.layer_var = ctk.StringVar(value="1")
        self.layer_menu = ctk.CTkOptionMenu(adv, values=["1", "2", "3", "4"], variable=self.layer_var, width=120)
        self.layer_menu.grid(row=4, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(
            adv,
            text="Deploy uses your .joblib model if available; otherwise RT60 rule (Dead <0.2 • Neutral 0.2–0.4 • Hot >0.4).",
            text_color=TEXT_DIM, font=("Segoe UI", 11)
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 10))

        adv.grid_columnconfigure(1, weight=1)

        # Controls
        controls = ctk.CTkFrame(self, fg_color=DARK2, corner_radius=12)
        controls.pack(fill="x", padx=10, pady=(0, 6))

        self.start_btn = ctk.CTkButton(
            controls, text="▶ Start Capture", command=self.start,
            fg_color=PINK, hover_color=PINK_HOVER, corner_radius=10, width=140
        )
        self.start_btn.pack(side="left", padx=8, pady=8)

        self.stop_btn = ctk.CTkButton(
            controls, text="■ Stop", command=self.stop,
            fg_color="#282838", hover_color="#34344a", corner_radius=10, width=70
        )
        self.stop_btn.pack(side="left", padx=6, pady=8)

        self.deploy_btn = ctk.CTkButton(
            controls, text="🚀 Deploy Class to Sheet", command=self.deploy_to_gsheet,
            fg_color=PINK, hover_color=PINK_HOVER, corner_radius=10, width=220
        )
        self.deploy_btn.pack(side="left", padx=8, pady=8)

        ctk.CTkLabel(controls, text="Layer:", text_color=TEXT_DIM).pack(side="left", padx=(6, 2))
        self.layer_short_menu = ctk.CTkOptionMenu(controls, values=["1", "2", "3", "4"], variable=self.layer_var, width=80)
        self.layer_short_menu.pack(side="left", padx=(0, 8))

        self.progress = ctk.CTkProgressBar(
            controls, width=300, progress_color=PINK, fg_color="#1b1b23"
        )
        self.progress.set(0)
        self.progress.pack(side="right", padx=10, pady=10)

        # LOG
        log_wrap = ctk.CTkFrame(self, fg_color=DARK2, corner_radius=12)
        log_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log = ctk.CTkTextbox(
            log_wrap, fg_color="#0f0f15", text_color=PINK,
            corner_radius=10, wrap="word", font=("Consolas", 10)
        )
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

        self._write("💗 Ready — dgpo4 backend. Rows default to 200 (1.8° resolution, 2 sensors). Choose 1°/s or 5°/s.")

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

        # rows
        try:
            count = int(self.count_var.get() or MAX_ROWS)
        except Exception:
            count = MAX_ROWS
        count = max(1, min(count, MAX_ROWS))

        # speed -> interval (dgpo4 expects angle-speed as int 1 or 5)
        try:
            speed = int(self.speed_var.get())
        except Exception:
            speed = 5
        if speed not in (1, 5):
            speed = 5

        # ✅ interval now based on float ANGLE_STEP (1.8)
        interval = float(ANGLE_STEP) / float(speed)

        port = (self.port_var.get() or "").strip()

        # ✅ UPDATED CLI: removed --max-angle (dgpo4 no longer accepts it)
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
        ]

        # ✅ Prevent live classification; only classify on Deploy
        cmd.append("--no-predict")

        if self.sim_var.get():
            cmd.append("--simulate")
        if self.skip_var.get():
            cmd.append("--skip-gsheets")

        # Pass sheet index (0-based) to backend so it uploads to the selected layer
        try:
            sheet_index = max(0, int(self.layer_var.get()) - 1)
        except Exception:
            sheet_index = 0
        cmd.extend(["--sheet-index", str(sheet_index)])

        self._write(f"▶ Running: {' '.join(cmd)}")

        self.stop_requested = False
        self.progress.set(0)

        def run():
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
                    self.progress.set((time.time() - start_t) % 1.0)

                self.proc.wait()
                self._write("✅ Done!" if not self.stop_requested else "⏹ Stopped by user.")
            except FileNotFoundError:
                self._write(f"❌ dgpo4.py not found!\nPath: {SCRIPT_PATH}")
            except Exception as e:
                self._write(f"❌ Error: {e}")
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
        """
        Reads Google Sheet, uses model if present (fallback: rule), writes back.

        Preferred final uploaded schema (if dgpo3-style data exists):
            sensor, angle, rt60, utv, utvh, dB, class

        Fallback schema (only if dgpo3 fields do not exist at all):
            angle, reverberation, ultrasonicValue, db, Classification
        """
        if gspread is None or Credentials is None or pd is None:
            self._write("❌ Missing packages. Install:\n  pip install gspread google-auth pandas joblib")
            return

        sheet_url = self.sheet_var.get().strip()
        json_path = self.json_var.get().strip()
        model_path = self.model_var.get().strip()

        if not sheet_url:
            self._write("❌ Provide a Google Sheet URL.")
            return
        if not json_path or not os.path.isfile(json_path):
            self._write("❌ Service Account JSON not found at the given path.")
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

                dgpo3_cols = ["sensor", "angle", "rt60", "utv", "utvh", "dB", "class"]
                canonical_cols = ["angle", "reverberation", "ultrasonicValue", "db", "Classification"]

                has_dgpo3 = any(c in df_raw.columns for c in ("sensor", "rt60", "utv", "utvh", "dB", "class"))

                df = df_raw.copy()

                # Build df_work WITHOUT renaming away dgpo3 fields
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
                elif has_dgpo3:
                    df["class"] = df["Classification"]

                if has_dgpo3:
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

                    out_cols = dgpo3_cols
                    self._write("→ Uploading updated sheet (dgpo3 schema: sensor, angle, rt60, utv, utvh, dB, class)…")
                else:
                    for c in canonical_cols:
                        if c not in df_work.columns:
                            df_work[c] = ""
                    out_cols = canonical_cols
                    df = df_work
                    self._write("→ Uploading updated sheet (canonical schema: angle, reverberation, ultrasonicValue, db, Classification)…")

                out_df = df[out_cols].copy()

                ws.clear()
                ws.update([out_cols] + out_df.astype(object).values.tolist())

                self._write("✅ Deploy complete. Sheet updated.")
            except Exception as e:
                self._write(f"❌ Deploy error: {e}")
            finally:
                self.deploy_btn.configure(state="normal")

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()