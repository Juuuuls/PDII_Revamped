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
// Set to 0 so Arduino sends the raw ultrasonic distance without subtracting 8 cm
#define UTV_OFFSET_CM 0
#define CEILING_OFFSET_CM 100
#define APPLY_CEILING_OFFSET 0

// ---------- FIXED INTERNAL STOP TIMELINE ----------
#define AUDIO_SETTLE_MS 120

// ---------- AUDIO WINDOWS ----------
// Keep this at 1000 so rt60 stays in 0.000 to 1.000 seconds
#define RT60_WINDOW_MS 1000
#define DB_RMS_SAMPLES 320

// ---------- MIC SETTINGS ----------
#define MIC_CENTER      512
#define MIC_SOFT_GAIN   4.5f
#define MIC_NOISE_FLOOR 2.0f

// ---------- PEAK SENSITIVITY ----------
// Higher = less sensitive to tiny noise changes
#define PEAK_MARGIN 1 //5 default, 8 is safer for noisy environments

// ---------- SETUP ----------
void setup() {
  Serial.begin(115200);
  Serial.println("sensorNumber,angledeg,rawDistanceCm,distanceCm,heightCm,rt60,dB");
}

// ---------- SMALL HELPERS ----------
static void sortInt(int *a, int n) {
  for (int i = 0; i < n - 1; i++) {
    for (int j = i + 1; j < n; j++) {
      if (a[j] < a[i]) {
        int t = a[i];
        a[i] = a[j];
        a[j] = t;
      }
    }
  }
}

static long clampCm(long cm) {
  if (cm < 0) return 0;
  if (cm > US_MAX_CM) return US_MAX_CM;
  return cm;
}

// ---------- ULTRASONIC MEDIAN ----------
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

// ---------- RT60 (RAW PEAK TIME, LESS SENSITIVE) ----------
float estimateRT60_peakTimeSeconds(int loudnessPin) {
  unsigned long startTime = millis();
  int peakVal = -1;
  unsigned long peakTimeMs = 0;

  while ((millis() - startTime) < RT60_WINDOW_MS) {
    int raw = analogRead(loudnessPin);
    unsigned long elapsed = millis() - startTime;

    if (raw > peakVal + PEAK_MARGIN) {
      peakVal = raw;
      peakTimeMs = elapsed;
    }
  }

  return peakTimeMs / 1000.0f;
}

// ---------- dB (PEAK-TO-PEAK + SOFTER CALIBRATION) ----------
float measureDB_rms() {
  int minVal = 1023;
  int maxVal = 0;

  for (int i = 0; i < DB_RMS_SAMPLES; i++) {
    int raw = analogRead(MIC_PIN);

    if (raw < minVal) minVal = raw;
    if (raw > maxVal) maxVal = raw;
  }

  float peakToPeak = (float)(maxVal - minVal);

  if (peakToPeak < 1.0f) peakToPeak = 1.0f;

  // relative level from raw mic swing
  float dBOut = 20.0f * log10(peakToPeak);

  // tuned so typical room readings stay closer to 50-70 dB
  float calibratedDB = 0.75f * dBOut + 36.0f;

  return calibratedDB;
}

// ---------- SINGLE SENSOR REPORT ----------
void measureAndReport(Ultrasonic& distanceSensor, Ultrasonic& heightSensor,
                      int loudnessPin, int sensorNumber, float angleDeg) {

  // 1) Ultrasonic FIRST
  long rawDistanceCm = readUltrasonicMedianRawCm(distanceSensor);
  long rawHeightCm   = readUltrasonicMedianRawCm(heightSensor);

  long distanceCm = clampCm(rawDistanceCm + (long)UTV_OFFSET_CM);

  long heightCm = rawHeightCm;
#if APPLY_CEILING_OFFSET
  heightCm = clampCm(rawHeightCm + (long)CEILING_OFFSET_CM);
#endif

  // 2) Fixed settle BEFORE audio
  delay(AUDIO_SETTLE_MS);

  // 3) Audio features
  float rt60Out = estimateRT60_peakTimeSeconds(loudnessPin);
  float dBOut   = measureDB_rms();

  // 4) Output CSV
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

  // Enforce progress along a 5-minute timeline
  unsigned long targetElapsed =
    (unsigned long)(((unsigned long)stopIndex * TARGET_TOTAL_MS) / (unsigned long)TOTAL_STOPS);

  while ((millis() - scanStartMs) < targetElapsed) {
    delay(5);
  }
}