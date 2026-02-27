# dgpo3.py
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='sklearn')

"""
Reverberation Taker (Arduino + Google Sheet + Prediction)

CSV / Sheet outputs EXACT columns:
    sensor, angle, rt60, utv, utvh, dB, class

SUPPORTED Arduino line formats:

(A) Dmain (YOUR CURRENT .ino, 7 fields) ✅
    "sensorNumber,angledeg,rawDistanceCm,distanceCm,heightCm,rt60,dB"
    Example: 1,0.0,132,124,238,0.312,67.45
    Mapping:
      utv  = distanceCm        (already offset-adjusted in Arduino)
      utvh = heightCm
      rt60 = rt60
      dB   = dB
    Note: rawDistanceCm is ignored for CSV output (debug-only).

(B) Cmain / earlier "NEW" (6 fields) ✅
    "sensorNumber,angledeg,distanceCm,heightCm,rt60Seconds,dB"
    Example: 1,0.0,124,238,0.312,67.45

(C) Older / Bmain (OLD, 4 fields) ✅
    "sensor,ultrasonic_cm,rt60Seconds,dB"
    Example: 1,132,0.287,66.92

Angle handling:
- If Arduino provides angledeg (7-field or 6-field), we use it.
- Otherwise (4-field), we generate angle IDs using the fallback mapping.

IMPORTANT FIX (your request):
- Two sensors are 180° apart (facing away from each other).
- If you use 1.8° resolution: 360/1.8 = 200 unique directions.
- With two sensors, you only need 180° worth of servo stops (100 stops),
  but you will still LOG 200 ROWS (2 sensors per stop).
- This code now generates that properly and sets max_rows accordingly.
"""

import argparse, subprocess, shutil, serial, csv, time, os, random, re, math
from datetime import datetime
import joblib

# Optional: Google Sheets
import gspread
from google.oauth2.service_account import Credentials


# ---------------- Arduino helpers ----------------
def has_arduino_cli() -> bool:
    return shutil.which("arduino-cli") is not None

def try_upload(sketch_path: str, board: str, port: str) -> bool:
    if not has_arduino_cli():
        print("arduino-cli not found in PATH; skipping upload.")
        return False
    try:
        print("Compiling sketch...")
        subprocess.run(["arduino-cli", "compile", "--fqbn", board, sketch_path], check=True)
        print("Uploading sketch...")
        subprocess.run(["arduino-cli", "upload", "-p", port, "--fqbn", board, sketch_path], check=True)
        print("Upload complete. Waiting for Arduino to reset…")
        time.sleep(3)
        return True
    except subprocess.CalledProcessError as e:
        print("Upload/compile failed:", e)
        return False

def _is_number(s: str) -> bool:
    try:
        float(str(s).strip())
        return True
    except Exception:
        return False

def generate_simulated_reading_v4() -> str:
    """Old Bmain simulation: sensor, ultrasonic, rt60, dB"""
    sensor = random.choice([1, 2])
    ultrasonic = round(random.uniform(2.0, 400.0), 2)
    rt60 = round(random.uniform(0.10, 1.00), 3)  # seconds
    dB = round(random.uniform(40.0, 90.0), 2)
    return f"{sensor},{ultrasonic},{rt60},{dB}"

def generate_simulated_reading_dmain(sensor: int, angle: float) -> str:
    """Dmain simulation (7 fields): sensor, angledeg, rawDistanceCm, distanceCm, heightCm, rt60, dB"""
    raw_distance = round(random.uniform(2.0, 400.0), 2)
    distance = max(0.0, raw_distance - 8.0)  # simulate Arduino offset
    height = round(random.uniform(10.0, 300.0), 2)
    rt60 = round(random.uniform(0.10, 1.00), 3)
    dB = round(random.uniform(40.0, 90.0), 2)
    return f"{sensor},{angle:.1f},{raw_distance},{distance},{height},{rt60},{dB}"

def generate_simulated_reading_v6(sensor: int, angle: float) -> str:
    """Cmain-style simulation: sensor, angledeg, distance, height, rt60, dB"""
    distance = round(random.uniform(2.0, 400.0), 2)
    height = round(random.uniform(10.0, 300.0), 2)
    rt60 = round(random.uniform(0.10, 1.00), 3)
    dB = round(random.uniform(40.0, 90.0), 2)
    return f"{sensor},{angle:.1f},{distance},{height},{rt60},{dB}"


# ---------------- Google Sheets helpers ----------------
def resolve_service_json(path_from_args: str):
    if path_from_args and os.path.isfile(path_from_args):
        return path_from_args
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(script_dir, "service_account.json")
    if os.path.isfile(candidate):
        return candidate
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if env and os.path.isfile(env):
        return env
    return None

def upload_to_existing_sheet(csv_path, sheet_url, service_json, sheet_index: int = 0):
    """Upload CSV data to an existing Google Sheet."""
    print("[INFO] Starting Google Sheets upload process...")

    if not os.path.isfile(service_json):
        raise FileNotFoundError(f"Service account JSON not found: {service_json}")
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not m:
        raise ValueError("Invalid Google Sheet URL – must contain /spreadsheets/d/<ID>")
    sheet_id = m.group(1)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(service_json, scopes=scopes)
    client = gspread.authorize(creds)

    sh = client.open_by_key(sheet_id)
    sheets = sh.worksheets()
    if sheet_index < 0:
        sheet_index = 0
    if sheet_index >= len(sheets):
        for i in range(len(sheets), sheet_index + 1):
            title = f"Sheet{i+1}"
            sh.add_worksheet(title=title, rows=1000, cols=30)
        sheets = sh.worksheets()
    ws = sh.get_worksheet(sheet_index)

    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))
        ws.clear()
        ws.update(rows)
    print(f"[OK] Successfully uploaded {len(rows)} rows to {sheet_url}")


# ---------------- Prediction ----------------
class ZonePredictor:
    def __init__(self, model_path: str, default_frequency: float = 1000.0):
        self.enabled = False
        self.default_frequency = float(default_frequency)
        self.feature_order = ["frequency", "RT60", "RT60_deviation"]

        if not model_path or not os.path.isfile(model_path):
            print("ZonePredictor: model not provided/found. Classification will be blank.")
            return
        try:
            bundle = joblib.load(model_path)
            self.model = bundle["model"] if isinstance(bundle, dict) and "model" in bundle else bundle
            if isinstance(bundle, dict):
                self.feature_order = bundle.get("feature_order", self.feature_order)
            self.enabled = True
            print(f"[OK] Zone model loaded: {model_path}")
            print(f"     Features: {self.feature_order}")
        except Exception as e:
            print("[ERROR] ZonePredictor: failed to load model:", e)

    def predict(self, rt60: float, frequency: float | None = None) -> str:
        if not self.enabled:
            return ""
        if frequency is None:
            frequency = self.default_frequency
        rt60_dev = abs(float(rt60) - 0.3)
        feats = {
            "frequency": float(frequency),
            "RT60": float(rt60),
            "RT60_deviation": float(rt60_dev),
        }
        row = [[feats[k] for k in self.feature_order]]
        try:
            pred = self.model.predict(row)[0]
            return str(pred)
        except Exception:
            return ""


# ---------------- Parsing ----------------
def parse_arduino_line(line: str):
    """
    Returns dict with keys:
      sensor(int), angle(float|None), utv(float), utvh(float|None), rt60(float), dB(float),
      raw_utv(float|None)
    or None if invalid/unparseable.

    Supports:
      - Dmain 7-field: sensor,angle,rawDistanceCm,distanceCm,heightCm,rt60,dB
      - Cmain 6-field: sensor,angle,distanceCm,heightCm,rt60,dB
      - Bmain 4-field: sensor,utv,rt60,dB
    """
    s = (line or "").strip()
    if not s:
        return None

    low = s.lower()
    if "sensornumber" in low or ("sensor" in low and "angle" in low):
        return None

    parts = [p.strip() for p in s.split(",") if p.strip() != ""]
    if len(parts) < 4:
        return None

    # Dmain 7-field
    if len(parts) >= 7 and all(_is_number(p) for p in parts[:7]):
        sensor = int(float(parts[0]))
        angle = float(parts[1])
        raw_distance = float(parts[2])
        distance = float(parts[3])
        height = float(parts[4])
        rt60 = float(parts[5])
        dB = float(parts[6])
        return {
            "sensor": sensor,
            "angle": angle,
            "utv": distance,
            "utvh": height,
            "rt60": rt60,
            "dB": dB,
            "raw_utv": raw_distance,
        }

    # Cmain 6-field
    if len(parts) >= 6 and all(_is_number(p) for p in parts[:6]):
        sensor = int(float(parts[0]))
        angle = float(parts[1])
        distance = float(parts[2])
        height = float(parts[3])
        rt60 = float(parts[4])
        dB = float(parts[5])
        return {"sensor": sensor, "angle": angle, "utv": distance, "utvh": height, "rt60": rt60, "dB": dB, "raw_utv": None}

    # Bmain 4-field
    if len(parts) >= 4 and all(_is_number(p) for p in parts[:4]):
        sensor = int(float(parts[0]))
        utv = float(parts[1])
        rt60 = float(parts[2])
        dB = float(parts[3])
        return {"sensor": sensor, "angle": None, "utv": utv, "utvh": None, "rt60": rt60, "dB": dB, "raw_utv": None}

    return None


# ---------------- Angle sequence (FIXED) ----------------
def build_measurement_sequence(angle_step: float):
    """
    Two sensors are 180° apart.
    To cover full 360° at 'angle_step' resolution, you need:
      N = 360 / angle_step unique directions.

    Because you measure TWO directions per servo stop (sensor1 and sensor2 opposite),
    servo only needs N/2 stops across 180°.

    We generate 180° worth of base angles:
      base = 0, step, 2*step, ... , (180-step)
    and for each base:
      (sensor 1, base)
      (sensor 2, base + 180 mod 360)

    For step=1.8 => 100 stops => 200 rows.
    """
    step = float(angle_step)
    if step <= 0:
        raise ValueError("angle_step must be > 0")

    # Must divide 360 cleanly for a perfect map; allow small float noise.
    N = int(round(360.0 / step))
    # If N is odd, you can't split perfectly into 180° stops for two sensors.
    if N % 2 != 0:
        raise ValueError(f"360/angle_step must be even for 2-sensor opposite mapping. Got N={N} for step={step}")

    stops = N // 2  # number of servo positions across 180°
    seq = []
    for i in range(stops):
        base = round(i * step, 1)
        seq.append((1, base))
        opp = (base + 180.0) % 360.0
        seq.append((2, round(opp, 1)))

    return seq


# ---------------- Main ----------------
def main():
    parser = argparse.ArgumentParser(description="Read ultrasonic+height+RT60+dB → CSV → Google Sheet (optional)")

    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--board", default="arduino:avr:uno")

    # Point this to your Dmain .ino
    parser.add_argument("--sketch", default=r"C:/Users/Ju/OneDrive/Documents/Project_Design_I_Files/pYcODE2/Dmain/Dmain.ino")

    # Angle settings
    # IMPORTANT: allow float steps like 1.8
    parser.add_argument("--angle-step", type=float, default=1.8)
    parser.add_argument("--angle-speed", type=int, choices=[1, 5], default=5)

    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--sheet-index", type=int, default=0)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--no-upload", action="store_true")

    parser.add_argument("--out-dir", default=None)

    parser.add_argument("--skip-gsheets", action="store_true")
    parser.add_argument("--service-json",
        default=r"C:/Users/Ju/OneDrive/Documents/Project_Design_I_Files/pYcODE2/projectdesignt6-b8c2872f2067.json")
    parser.add_argument("--sheet-link",
        default="https://docs.google.com/spreadsheets/d/1OAfQI6MwheL6wIes1EhGjak3G1jSVLFGppmzqTL9MWQ/edit?usp=sharing")

    parser.add_argument("--model-path",
        default=r"C:/Users/Ju/OneDrive/Documents/Project_Design_I_Files/pYcODE2/reverb_zone_rf.joblib")
    parser.add_argument("--freq", type=float, default=1000.0)
    parser.add_argument("--no-predict", action="store_true")

    args = parser.parse_args()

    if not args.no_upload and not args.simulate:
        try_upload(args.sketch, args.board, args.port)

    # ✅ FIXED: build correct 2-sensor 360° coverage sequence
    measurement_sequence = build_measurement_sequence(args.angle_step)

    max_rows = len(measurement_sequence)  # step=1.8 -> 200 rows
    rows_needed = args.count if (args.count and args.count > 0) else max_rows
    rows_needed = min(rows_needed, max_rows)

    # Interval (pacing)
    interval = args.interval if (args.interval and args.interval > 0) else (float(args.angle_step) / float(args.angle_speed))

    predictor = ZonePredictor(args.model_path, default_frequency=args.freq)
    do_predict = predictor.enabled and (not args.no_predict)

    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(out_dir, f"peaks_{ts}.csv")

    header = ["sensor", "angle", "rt60", "utv", "utvh", "dB", "class"]

    written = 0
    last_time = time.time()

    ser = None
    using_serial = not args.simulate
    if using_serial:
        try:
            ser = serial.Serial(
                port=args.port,
                baudrate=args.baud,
                timeout=5,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )
            if ser.is_open:
                ser.close()
            time.sleep(0.5)
            ser.open()
            try:
                ser.setDTR(False)
                time.sleep(0.4)
                ser.setDTR(True)
            except Exception as e:
                print(f"DTR reset failed (non-critical): {e}")
            time.sleep(1.2)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            print(f"[OK] Opened {args.port} @ {args.baud}")
        except Exception as e:
            print(f"[ERROR] Serial open failed: {e} -> Simulate mode")
            if ser and ser.is_open:
                try:
                    ser.close()
                except Exception:
                    pass
            using_serial = False

    # Print scan plan
    uniq_dirs = int(round(360.0 / float(args.angle_step)))
    servo_stops = uniq_dirs // 2
    print(f"(2-sensor) angle_step={args.angle_step}° → unique directions={uniq_dirs} → servo stops={servo_stops} → max rows={max_rows}")
    print(f"Angle speed: {args.angle_speed}°/s  → interval {interval:.3f}s per row")
    print(f"Target rows this run: {rows_needed}")
    if do_predict:
        print("[INFO] Zone prediction ENABLED")

    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)

        idx = 0
        while written < rows_needed:
            current_sensor, fallback_angle = measurement_sequence[idx]
            idx = (idx + 1) % len(measurement_sequence)

            now = time.time()
            elapsed = now - last_time
            if elapsed < interval:
                time.sleep(interval - elapsed)
            last_time = time.time()

            try:
                if using_serial:
                    raw_data = ser.readline()
                    if not raw_data:
                        print("(timeout waiting for data...)")
                        continue
                    line = raw_data.decode(errors="ignore").strip()
                    if not line:
                        print("(empty line received)")
                        continue
                else:
                    line = generate_simulated_reading_dmain(current_sensor, fallback_angle)

                parsed = parse_arduino_line(line)
                if not parsed:
                    print(f"(skip) Unrecognized line: {line}")
                    continue

                sensor = parsed["sensor"]
                utv = parsed["utv"]
                utvh = parsed["utvh"]
                rt60 = parsed["rt60"]
                dB = parsed["dB"]

                angle_val = parsed["angle"] if parsed["angle"] is not None else fallback_angle

                label = predictor.predict(rt60=rt60, frequency=args.freq) if do_predict else ""

                utvh_str = "" if (utvh is None) else f"{utvh}"

                row = [str(sensor), f"{angle_val}", f"{rt60}", f"{utv}", utvh_str, f"{dB}", label]
                w.writerow(row)
                f.flush()
                written += 1

                raw_utv = parsed.get("raw_utv", None)
                if raw_utv is not None:
                    print(
                        f"Sensor {sensor} at {angle_val}°: "
                        f"raw_utv={raw_utv}, utv={utv}, utvh={utvh_str}, rt60={rt60}s, dB={dB}, class='{label}'"
                    )
                else:
                    print(
                        f"Sensor {sensor} at {angle_val}°: "
                        f"utv={utv}, utvh={utvh_str}, rt60={rt60}s, dB={dB}, class='{label}'"
                    )

            except Exception as e:
                print(f"Unexpected error: {e}")
                continue

    if ser:
        try:
            ser.close()
        except Exception:
            pass

    print(f"[OK] Saved {written} rows -> {csv_path}")

    if args.skip_gsheets:
        print("[INFO] Skipping Google Sheets upload (--skip-gsheets).")
        return

    sa_path = resolve_service_json(args.service_json)
    if not sa_path:
        print("[ERROR] Sheets upload skipped: service-account JSON not found.")
        return

    try:
        upload_to_existing_sheet(csv_path, args.sheet_link, sa_path, args.sheet_index)
    except Exception as e:
        print("[ERROR] Google Sheets upload failed:", e)
        print("[TIP] Share your Sheet with the service account email (Editor).")


if __name__ == "__main__":
    main()