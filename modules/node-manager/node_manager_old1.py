import os
import json
import time
import threading
import logging
import paho.mqtt.client as mqtt


# =========================
# Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S"
)

log = logging.getLogger()


# =========================
# MQTT config (.env)
# =========================

MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")


# =========================
# Node storage
# =========================

nodes = {}
HEARTBEAT_TIMEOUT = 30
table_dirty = True


def now():
    return int(time.time())


# =========================
# Table display
# =========================

def print_table():

    global table_dirty

    if not table_dirty:
        return

    table_dirty = False

    log.info("-" * 70)
    log.info(f"{'NODE':<12}{'FW':<10}{'UPTIME':<10}{'LAST SEEN':<10}SENSORS")
    log.info("-" * 70)

    if not nodes:
        log.info("No nodes connected")
        log.info("-" * 70)
        return

    for node_id, node in nodes.items():

        fw = node.get("firmware") or "?"
        uptime = node.get("uptime") or "?"
        last_seen = f"{now() - node['last_seen']}s"

        sensors = " ".join(
            f"{k}={v}" for k, v in node["sensors"].items()
        )

        log.info(f"{node_id:<12}{fw:<10}{uptime:<10}{last_seen:<10}{sensors}")

    log.info("-" * 70)


# =========================
# MQTT callbacks
# =========================

def on_connect(client, userdata, flags, rc):

    if rc == 0:

        log.info("Connected to MQTT broker")

        client.subscribe("swan-hub/node/+/heartbeat")
        client.subscribe("swan-hub/node/+/+")

        log.info("Subscribed to node topics")

    else:

        log.error(f"MQTT connection failed: {rc}")


def on_message(client, userdata, msg):

    global table_dirty

    topic = msg.topic.split("/")
    payload = msg.payload.decode()

    if len(topic) < 4:
        return

    node_id = topic[2]
    subtopic = topic[3]

    # -----------------------
    # HEARTBEAT
    # -----------------------

    if subtopic == "heartbeat":

        #payload_clean = payload.strip()

        # Remove outer quotes if present
        #if (payload_clean.startswith("'") and payload_clean.endswith("'")) or \
        #(payload_clean.startswith('"') and payload_clean.endswith('"')):
        #    log.warning("Payload has extra quotes, stripping them")
        #    payload_clean = payload_clean[1:-1]

        # Decode escaped newlines and other escaped chars
        #payload_clean = payload_clean.encode("utf-8").decode("unicode_escape")

        try:
            data = json.loads(payload)
            log.verbose(f"Heartbeat from {node_id}: {data}")

        except:
            log.warning(f"Invalid heartbeat from {node_id}: {payload!r}")
            return

        sensors = data.get("sensors", [])

        if node_id not in nodes:
            log.info(f"Node discovered: {node_id}")

        existing = nodes.get(node_id, {}).get("sensors", {})

        nodes[node_id] = {
            "last_seen": now(),
            "firmware": data.get("firmware"),
            "uptime": data.get("uptime"),
            "sensors": {s: existing.get(s) for s in sensors}
        }

        table_dirty = True

    # -----------------------
    # SENSOR UPDATE
    # -----------------------

    else:

        sensor = subtopic

        try:
            value = json.loads(payload).get("value")
        except:
            log.warning(f"Invalid sensor update from {node_id} for {sensor}: {payload!r}")
            value = payload

        if node_id not in nodes:

            nodes[node_id] = {
                "last_seen": now(),
                "firmware": None,
                "uptime": None,
                "sensors": {}
            }

            log.info(f"Node discovered: {node_id}")

        nodes[node_id]["last_seen"] = now()

        old = nodes[node_id]["sensors"].get(sensor)

        if old != value:

            nodes[node_id]["sensors"][sensor] = value
            table_dirty = True


# =========================
# Offline detection
# =========================

def monitor_nodes():

    global table_dirty

    while True:

        time.sleep(5)

        for node_id in list(nodes.keys()):

            if now() - nodes[node_id]["last_seen"] > HEARTBEAT_TIMEOUT:

                log.warning(f"{node_id} went offline")

                del nodes[node_id]
                table_dirty = True

        print_table()


# =========================
# Main
# =========================

log.info("Starting SWAN Node Manager")

client = mqtt.Client()

if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

client.on_connect = on_connect
client.on_message = on_message

log.info(f"Connecting to MQTT broker {MQTT_BROKER}:{MQTT_PORT}")

client.connect(MQTT_BROKER, MQTT_PORT)

threading.Thread(target=monitor_nodes, daemon=True).start()

client.loop_forever()