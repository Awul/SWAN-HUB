import os
import json
import time
import logging
from datetime import datetime
import paho.mqtt.client as mqtt

# =========================
# Colored Logging
# =========================

RESET = "\033[0m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, RESET)
        record.msg = f"{color}{record.msg}{RESET}"
        return super().format(record)

handler = logging.StreamHandler()
handler.setFormatter(ColorFormatter(
    "%(asctime)s │ %(levelname)s │ %(message)s",
    "%H:%M:%S"
))

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# =========================
# MQTT Configuration
# =========================

MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

# =========================
# Node Storage
# =========================

nodes = {}

HEARTBEAT_TIMEOUT = 30

# =========================
# Utility
# =========================

def now():
    return int(time.time())


def print_overview():
    """Pretty print all connected nodes."""
    print("\n" + CYAN + "────────────── ACTIVE NODES ──────────────" + RESET)

    if not nodes:
        print("No nodes connected\n")
        return

    for node_id, node in nodes.items():

        last_seen = now() - node["last_seen"]

        print(
            f"{MAGENTA}{node_id}{RESET} │ "
            f"fw={node.get('firmware','?')} │ "
            f"uptime={node.get('uptime','?')}s │ "
            f"last_seen={last_seen}s"
        )

        if node["sensors"]:
            for sensor, value in node["sensors"].items():
                print(f"   ├─ {sensor}: {value}")

    print(CYAN + "──────────────────────────────────────────\n" + RESET)


# =========================
# MQTT Callbacks
# =========================

def on_connect(client, userdata, flags, rc):

    if rc == 0:
        logger.info("Connected to MQTT broker")

        client.subscribe("swan-hub/node/+/heartbeat")
        client.subscribe("swan-hub/node/+/+")

        logger.info("Subscribed to node topics")

    else:
        logger.error(f"MQTT connection failed: {rc}")


def on_message(client, userdata, msg):

    topic = msg.topic.split("/")
    payload = msg.payload.decode()

    if len(topic) < 4:
        return

    node_id = topic[2]
    subtopic = topic[3]

    # =========================
    # HEARTBEAT
    # =========================

    if subtopic == "heartbeat":

        try:
            data = json.loads(payload)

            sensors = data.get("sensors", [])

            nodes[node_id] = {
                "last_seen": now(),
                "firmware": data.get("firmware"),
                "uptime": data.get("uptime"),
                "sensors": {s: None for s in sensors}
            }

            logger.info(
                f"Heartbeat from {node_id} │ "
                f"fw={data.get('firmware')} │ "
                f"uptime={data.get('uptime')}s │ "
                f"sensors={sensors}"
            )

            print_overview()

        except Exception as e:
            logger.warning(f"Invalid heartbeat from {node_id}")

    # =========================
    # SENSOR UPDATE
    # =========================

    else:

        sensor = subtopic

        try:
            data = json.loads(payload)
            value = data.get("value")

        except:
            value = payload

        if node_id not in nodes:
            nodes[node_id] = {
                "last_seen": now(),
                "firmware": None,
                "uptime": None,
                "sensors": {}
            }

        nodes[node_id]["last_seen"] = now()
        nodes[node_id]["sensors"][sensor] = value

        logger.info(f"{node_id} │ {sensor} = {value}")


# =========================
# Offline Detection
# =========================

def monitor_nodes():

    while True:

        time.sleep(5)

        for node_id in list(nodes.keys()):

            if now() - nodes[node_id]["last_seen"] > HEARTBEAT_TIMEOUT:

                logger.warning(f"{node_id} went offline")

                del nodes[node_id]

                print_overview()


# =========================
# Main
# =========================

logger.info("Starting SWAN Node Manager")

client = mqtt.Client()

if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

client.on_connect = on_connect
client.on_message = on_message

client.connect(MQTT_BROKER, MQTT_PORT)

import threading
threading.Thread(target=monitor_nodes, daemon=True).start()

client.loop_forever()
