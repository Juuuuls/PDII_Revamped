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
#define SAMPLE_WINDOW 50   // ms

// ---------- 4-SECOND LOGGING SCHEDULE ----------
enum Phase { S1_MEASURE, PAUSE1, S2_MEASURE, PAUSE2 };
Phase phase = S1_MEASURE;

unsigned long lastPhaseTime = 0;
const unsigned long phaseDurationMs = 1000;

// ---------- ANGLE SWEEP SETTINGS ----------
const int TOTAL_STOPS = 100;
const float STEP_DEG = 180.0f / (float)TOTAL_STOPS; // 1.8 deg
int stopIndex = 0;
bool finished = false;

// ---------- ULTRASONIC OPTIMIZATION ----------
#define US_SETTLE_MS 60
#define US_SAMPLES   5
#define US_MAX_CM    600

// ---------- UTV CALIBRATION ----------
#define UTV_OFFSET_CM (-8)   // subtract 8 cm from distance ultrasonic outputs

// ---------- SETUP ----------
void setup() {
  Serial.begin(115200);
  // Added rawDistanceCm so you can verify the deduction is happening
  Serial.println("sensorNumber,angledeg,rawDistanceCm,distanceCm,heightCm,rt60,dB");
  lastPhaseTime = millis();
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

// ---------- "RT60" (peak-time within 1 second) ----------
float estimateRT60_peakTimeSeconds(int loudnessPin) {
  unsigned long startTime = millis();
  int peakValue = -1;
  float peakTime = 0.0f;

  while (millis() - startTime < 1000) {
    int loudnessValue = analogRead(loudnessPin);
    float timeInSeconds = (millis() - startTime) / 1000.0f;

    if (loudnessValue > peakValue) {
      peakValue = loudnessValue;
      peakTime = timeInSeconds;
    }
    delay(5);
  }
  return peakTime;
}

// ---------- dB FUNCTION ----------
float measureDB() {
  unsigned long startMillis = millis();
  int signalMax = 0;
  int signalMin = 1023;

  while (millis() - startMillis < SAMPLE_WINDOW) {
    int sample = analogRead(MIC_PIN);
    if (sample > signalMax) signalMax = sample;
    if (sample < signalMin) signalMin = sample;
  }

  int peakToPeak = signalMax - signalMin;
  if (peakToPeak < 1) peakToPeak = 1;

  return 20.0f * log10((float)peakToPeak);
}

// ---------- MAIN MEASURE ----------
void measureAndReport(Ultrasonic& distanceSensor, Ultrasonic& heightSensor,
                      int loudnessPin, int sensorNumber, float angleDeg) {

  // RAW medians
  long rawDistanceCm = readUltrasonicMedianRawCm(distanceSensor);
  delay(US_SETTLE_MS);
  long heightCm      = readUltrasonicMedianRawCm(heightSensor);

  // Apply -8 AFTER median (guaranteed)
  long distanceCm = clampCm(rawDistanceCm + (long)UTV_OFFSET_CM);

  float rt60Seconds = estimateRT60_peakTimeSeconds(loudnessPin);
  float dB = measureDB();

  // sensorNumber,angledeg,rawDistanceCm,distanceCm,heightCm,rt60,dB
  Serial.print(sensorNumber); Serial.print(",");
  Serial.print(angleDeg, 1);  Serial.print(",");
  Serial.print(rawDistanceCm); Serial.print(",");
  Serial.print(distanceCm);   Serial.print(",");
  Serial.print(heightCm);     Serial.print(",");
  Serial.print(rt60Seconds, 3); Serial.print(",");
  Serial.println(dB, 2);
}

// ---------- LOOP ----------
void loop() {
  if (finished) return;

  unsigned long now = millis();

  // advance phase every 1 second
  if (now - lastPhaseTime >= phaseDurationMs) {
    lastPhaseTime = now;

    if (phase == S1_MEASURE)       phase = PAUSE1;
    else if (phase == PAUSE1)      phase = S2_MEASURE;
    else if (phase == S2_MEASURE)  phase = PAUSE2;
    else                           phase = S1_MEASURE;
  }

  // log exactly once per MEASURE phase
  static Phase lastLoggedPhase = PAUSE2;

  if ((phase == S1_MEASURE || phase == S2_MEASURE) && phase != lastLoggedPhase) {
    lastLoggedPhase = phase;

    float baseAngle = stopIndex * STEP_DEG;
    float angleDeg  = (phase == S1_MEASURE) ? baseAngle : (baseAngle + 180.0f);

    if (phase == S1_MEASURE) {
      measureAndReport(ultrasonic1, ceilingUltrasonic, loudnessPin1, 1, angleDeg);
    } else {
      measureAndReport(ultrasonic2, ceilingUltrasonic, loudnessPin2, 2, angleDeg);

      stopIndex++;
      if (stopIndex >= TOTAL_STOPS) finished = true;
    }
  }
}