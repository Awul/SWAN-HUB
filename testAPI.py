from fastapi import FastAPI
import numpy as np
import logging
import random

app = FastAPI(title="Minimal Calibration API")


logging.basicConfig(level=logging.INFO, format="%(asctime)s │ %(levelname)-7s │ %(message)s")
log = logging.getLogger(__name__)

# Store ongoing calibrations per sensor
ongoing_calibrations = {}

def read_sensor(sensor_id):
   return random.randint(0,10)

# Start calibration
# Hier sollte man den Sensor auslesen, nacher lesen wir die Werte über MQTT
# aber für jetzt können wir erstmal ne platzhalter funktion machen 
# (read_sensor(sensor_id) die dann einfach random Werte zurückgibt)
# bis ich mir überlegt hab wie die genauere architektur da aussehen soll.
@app.get("/calibration/start/{sensor_id}/{averages}")
def start_calibration(sensor_id: str, averages: int):
    ongoing_calibrations[sensor_id] = {"averages": averages, "steps": []}
    value = read_sensor(sensor_id) 
    log.info(f"Started calibration for '{sensor_id}' with {averages} averages")
    log.info(f"The sensor output has the value '{value}'")   
    return {"message": f"Started calibration for Sensor '{sensor_id}' with {averages} averages  and read value {value}"}

 
# Record a calibration step
# Hier sollen dann die n Messwerte genommen werden die auch für das GUI feedback
# in der antwort zurückgegeben werden. Wäre sonst noch nice die Standardabweichung zurückzugeben damit
# man ne idee für das Rauschen hat.
@app.get("/calibration/step/{sensor_id}/{ref_value}")
def calibration_step(sensor_id: str, ref_value: float):
    """
    measurements: comma-separated values, e.g. "24.9,25.0,25.1"
    The number of measurements must match the 'averages' from start.
    """
    # Hier lesen wir aus unserem ongoing_calibrations Dict die Anzahl der Messungen aus
    # (da sollen generell immer alle wichtigen infos drinstehen zum aktuellen kalibrier vorgang)
    averages = ongoing_calibrations[sensor_id]["averages"]
    measure = np.array([])
    offset = np.array([])

    # Collect n measurements here:
    for n in range(averages):
        value = read_sensor(sensor_id)
        measure = np.append(measure,value)

    avg_value = 1/averages * np.sum(measure)
    for m in range(averages):
        offset_temp = measure[m] - avg_value
        offset = np.append(offset, offset_temp)

    # Hier wird das Werte-Paar aus Referenzwert und gemessenem Durchschnittswert in die ongoing_calibrations Struktur gespeichert
    # Am ende soll daraus dann eine n Punkt kalibrierung berechnet werden. Da am besten mal schauen ob wir das begrenzen sollten
    # oder dynamisch halten wollen.
    ongoing_calibrations[sensor_id]["steps"].append((ref_value, avg_value, offset))
    log.info(f"Step recorded for '{sensor_id}': ref={ref_value}, avg_measured={avg_value}, offset= {offset}")
    return {"message": "Step recorded", "average": {avg_value}}

# Finish calibration
# Hier wird dann aus den gesammelten Werte-Paaren die Kalibrierung berechnet. Da kommt es jetzt auf das genaue kalibrierungsmodell an.
@app.get("/calibration/finish/{sensor_id}")
def finish_calibration(sensor_id: str):
    steps = ongoing_calibrations[sensor_id]["steps"]
    refs, raws, off = zip(*steps)
    # Hier als beispiel nen linearer Fit.
    scalar, offset = np.polyfit(raws, refs, 1)
    del ongoing_calibrations[sensor_id]


    log.info(f"Calibration finished for '{sensor_id}'. Scalar={scalar}, Offset={offset}")
    # Als Rückgabe an die API dann die Koeffizienten, am besten aber auch
    # an den Sensor mit ner Platzhalterfunktion send_calibration(sensor_id, coefficients)
    send_calibration(sensor_id, offset)
    
    return {
        "message": "Calibration finished",
        "coefficients": {"scalar": scalar, "offset": offset},
        "steps": [{"ref": r, "measured_avg": m, "offset": o} for r, m, o in steps]
    }

def send_calibration(sensor_id, coefficients):
    return {sensor_id, coefficients}


#Quickcalibration
#Beschreibung Quickcalibration einfügen zur besseren Lesbarkeit
@app.get("/calibration/quickcalibration/{averages}/{number_of_sensors}")
def quick_calibration(averages:int, number_of_sensors:int):
    # Anzahl der Averages wird aus allen Sensoren ausgemessen und der allgemeine Mittelwert bestimmt. Dann wird für jeden Sensor der Offset ermittelt und zurückgegeben
    measurements =np.array ([])
    avg_value = np.array([])
    # Schleife für Einlesen der n Sensoren
    for n in range(number_of_sensors):
        #Schleife für Einlesen der Messwerte
        for number in range(averages):
            sensor_value = read_sensor(n)
            measurements = np.append(measurements,sensor_value)

        avg_value = np.append(avg_value, 1/averages * np.sum(measurements))

    mean_value = 1/number_of_sensors * np.sum(avg_value)
    stdv = np.std(avg_value)

    #Schleife zum zurückgeben des Offsets
    for m in range(len(measurements)):
        offset = measurements[m] - mean_value
        send_calibration(m, offset)
    return {"message": f"Calibration for '{number_of_sensors}' Sensors done with '{mean_value}' as average value and a standard deviation of '{stdv}'."}


# Stop calibration
@app.get("/calibration/stop/{sensor_id}")
def stop_calibration(sensor_id: str):
    if sensor_id in ongoing_calibrations:
        del ongoing_calibrations[sensor_id]
        log.info(f"Calibration for '{sensor_id}' stopped")
    return {"message": f"Calibration for Sensor '{sensor_id}' stopped"}