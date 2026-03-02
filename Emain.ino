#include "Ultrasonic.h"
#include <math.h>

// ---------- PIN / SENSOR MAPPING ----------
// D2 - height ultrasonic
// D3 - distance ultrasonic 1
// D7 - distance ultrasonic 2
//
// A0 - loudness sensor 2
// A1 - loudness sensor 1
// A2 - MIC pin

// ---------- SENSOR DEFINITIONS ----------
int loudnessPin1 = A1;            // loudness sensor 1
Ultrasonic ultrasonic1(3);        // distance ultrasonic 1 on D3

int loudnessPin2 = A0;            // loudness sensor 2
Ultrasonic ultrasonic2(7);        // distance ultrasonic 2 on D7

Ultrasonic ceilingUltrasonic(2);  // height ultrasonic on D2

#define MIC_PIN A2

// ---------- ANGLE SWEEP SETTINGS ----------
const int   TOTAL_STOPS = 100;                          // 100 stops -> 200 rows
const float STEP_DEG    = 180.0f / (float)TOTAL_STOPS;  // 1.8 deg
int  stopIndex = 0;
bool finished  = false;

// ---------- TARGET TOTAL TIME (LOCKED) ----------
const unsigned long TARGET_TOTAL_MS = 300000UL; // 5 minutes

// ---------- ULTRASONIC OPTIMIZATION ----------
#define US_SETTLE_MS 25
#define US_SAMPLES   3
#define US_MAX_CM    600

// ---------- CALIBRATION ----------
#define UTV_OFFSET_CM (-8)    // subtract 8 cm from distance ultrasonic outputs
#define CEILING_OFFSET_CM 100
#define APPLY_CEILING_OFFSET 0

// ---------- FIXED INTERNAL STOP TIMELINE (STABILITY) ----------
// After ultrasonic, wait for ringing / motor vibration to die before audio.
#define AUDIO_SETTLE_MS 120

// ---------- AUDIO WINDOWS ----------
#define RT60_WINDOW_MS 350     // keep fast
#define DB_RMS_SAMPLES 320     // fixed N for consistent measurement time

// ---------- MIC SENSITIVITY (SOFTWARE) ----------
#define MIC_CENTER      512
#define MIC_SOFT_GAIN   3.0f
#define MIC_NOISE_FLOOR 2.0f

// ---------- SMOOTHING (STABILITY) ----------
#define DB_ALPHA   0.35f   // 0..1 ; higher = follows changes faster, lower = smoother
#define RT60_ALPHA 0.35f

// ---------- SETUP ----------
void setup() {
  Serial.begin(115200);
  Serial.println("sensorNumber,angledeg,rawDistanceCm,distanceCm,heightCm,rt60,dB");
}

// ---------- SMALL HELPERS ----------
static void sortInt(int *a, int n) {
  for (int i = 0; i < n - 1; i++) {
    for (int j = i + 1; j < n; j++) {
      if (a[j] < a[i]) { int t = a[i]; a[i] = a[j]; a[j] = t; }
    }
  }
}

static long clampCm(long cm) {
  if (cm < 0) return 0;
  if (cm > US_MAX_CM) return US_MAX_CM;
  return cm;
}

// Median-of-N ultrasonic read (raw, no offset)
long readUltrasonicMedianRawCm(Ultrasonic& u) {
  int vals[US_SAMPLES];

  for (int i = 0; i < US_SAMPLES; i++) {
    long cm = (long)u.MeasureInCentimeters();
    vals[i] = (int)clampCm(cm);
    delay(US_SETTLE_MS);
  }

  sortInt(vals, US_SAMPLES);
  return (long)vals[US_SAMPLES / 2];
}

// ---------- RT60 (PEAK TIME) WITH SIMPLE LOW-PASS FILTER ----------
float estimateRT60_peakTimeSeconds(int loudnessPin) {
  unsigned long startTime = millis();

  // simple IIR low-pass over analogRead to reduce jitter
  float filt = (float)analogRead(loudnessPin);

  float peakTime = 0.0f;
  float peakVal  = -1.0f;

  while (millis() - startTime < RT60_WINDOW_MS) {
    int raw = analogRead(loudnessPin);
    // low-pass: 0.25 new, 0.75 old
    filt = 0.75f * filt + 0.25f * (float)raw;

    float t = (millis() - startTime) / 1000.0f;
    if (filt > peakVal) {
      peakVal  = filt;
      peakTime = t;
    }
    delay(2);
  }
  return peakTime;
}

// ---------- dB (RMS) ----------
float measureDB_rms() {
  long sumSq = 0;

  for (int i = 0; i < DB_RMS_SAMPLES; i++) {
    int raw = analogRead(MIC_PIN);
    int x = raw - MIC_CENTER;
    sumSq += (long)x * (long)x;
  }

  float rms = sqrt((float)sumSq / (float)DB_RMS_SAMPLES);
  rms *= MIC_SOFT_GAIN;
  if (rms < MIC_NOISE_FLOOR) rms = MIC_NOISE_FLOOR;

  return 20.0f * log10(rms);
}

// ---------- SINGLE SENSOR REPORT (FIXED ORDER + SMOOTHING) ----------
void measureAndReport(Ultrasonic& distanceSensor, Ultrasonic& heightSensor,
                      int loudnessPin, int sensorNumber, float angleDeg) {

  // 1) Ultrasonic FIRST (consistent)
  long rawDistanceCm = readUltrasonicMedianRawCm(distanceSensor);
  long rawHeightCm   = readUltrasonicMedianRawCm(heightSensor);

  long distanceCm = clampCm(rawDistanceCm + (long)UTV_OFFSET_CM);

  long heightCm = rawHeightCm;
#if APPLY_CEILING_OFFSET
  heightCm = clampCm(rawHeightCm + (long)CEILING_OFFSET_CM);
#endif

  // 2) Fixed settle BEFORE audio (consistency)
  delay(AUDIO_SETTLE_MS);

  // 3) Audio features (fixed windows)
  float rt60Raw = estimateRT60_peakTimeSeconds(loudnessPin);
  float dBRaw   = measureDB_rms();

  // 4) Smooth per-sensor outputs (reduces random jumps)
  static bool  init1 = false, init2 = false;
  static float rt60_1 = 0, dB_1 = 0;
  static float rt60_2 = 0, dB_2 = 0;

  float rt60Out = rt60Raw, dBOut = dBRaw;

  if (sensorNumber == 1) {
    if (!init1) { init1 = true; rt60_1 = rt60Raw; dB_1 = dBRaw; }
    rt60_1 = (1.0f - RT60_ALPHA) * rt60_1 + RT60_ALPHA * rt60Raw;
    dB_1   = (1.0f - DB_ALPHA)   * dB_1   + DB_ALPHA   * dBRaw;
    rt60Out = rt60_1; dBOut = dB_1;
  } else {
    if (!init2) { init2 = true; rt60_2 = rt60Raw; dB_2 = dBRaw; }
    rt60_2 = (1.0f - RT60_ALPHA) * rt60_2 + RT60_ALPHA * rt60Raw;
    dB_2   = (1.0f - DB_ALPHA)   * dB_2   + DB_ALPHA   * dBRaw;
    rt60Out = rt60_2; dBOut = dB_2;
  }

  // Output CSV (stable timing + stable values)
  Serial.print(sensorNumber);     Serial.print(",");
  Serial.print(angleDeg, 1);      Serial.print(",");
  Serial.print(rawDistanceCm);    Serial.print(",");
  Serial.print(distanceCm);       Serial.print(",");
  Serial.print(heightCm);         Serial.print(",");
  Serial.print(rt60Out, 3);       Serial.print(",");
  Serial.println(dBOut, 2);
}

// ---------- LOOP (LOCKED TO 5 MIN TOTAL) ----------
void loop() {
  if (finished) return;

  static unsigned long scanStartMs = 0;
  if (scanStartMs == 0) scanStartMs = millis();

  float baseAngle = stopIndex * STEP_DEG;

  // One stop = two rows (sensor 1 then sensor 2)
  measureAndReport(ultrasonic1, ceilingUltrasonic, loudnessPin1, 1, baseAngle);
  measureAndReport(ultrasonic2, ceilingUltrasonic, loudnessPin2, 2, baseAngle + 180.0f);

  stopIndex++;
  if (stopIndex >= TOTAL_STOPS) {
    finished = true;
    Serial.println("SCAN_COMPLETE");
    return;
  }

  // Enforce progress along a 5-minute timeline:
  unsigned long targetElapsed =
    (unsigned long)(( (unsigned long)stopIndex * TARGET_TOTAL_MS) / (unsigned long)TOTAL_STOPS);

  while ((millis() - scanStartMs) < targetElapsed) {
    delay(5);
  }
}