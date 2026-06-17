# Currently no timestamp for the individual sensor readings are taken (so just the general online status is checked)

import os
import json
import time
import threading
import logging
import asyncio

import paho.mqtt.client as mqtt
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

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
STATUS_TOPIC = "swan-hub/nodes/status"

# =========================
# FastAPI
# =========================
app = FastAPI(title="SWAN Node Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # allow all origins for now; add front end url later!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connections = set()  # global

async def broadcast_update(data):
    # convert to JSON string once
    message = json.dumps(data)
    for ws in list(connections):
        try:
            await ws.send_text(message)
        except Exception:
            connections.remove(ws)

def now():
    return int(time.time())

# =========================
# WebSocket broadcast
# =========================


# =========================
# Publish node status
# =========================
def publish_status(client):
    payload = {
        node_id: {
            "firmware": node.get("firmware"),
            "uptime": node.get("uptime"),
            "last_seen": now() - node.get("last_seen", now()),
            "sensors": node.get("sensors")
        }
        for node_id, node in nodes.items()
    }

    client.publish(STATUS_TOPIC, payload=json.dumps(payload), retain=True)

    # push updates to websocket clients
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_update(payload))
    except RuntimeError:
        # no loop running (MQTT thread), schedule later
        pass

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

        try:

            data = json.loads(payload)
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

            log.info(
                f"Heartbeat from {node_id} │ "
                f"fw={data.get('firmware')} │ "
                f"uptime={data.get('uptime')}s │ "
                f"sensors={sensors}"
            )

            table_dirty = True

        except Exception:

            log.warning(
                f"Invalid heartbeat from {node_id}: {payload!r}"
            )

    # -----------------------
    # SENSOR UPDATE
    # -----------------------
    else:

        sensor = subtopic

        try:
            value = json.loads(payload).get("value")
        except Exception:

            log.warning(
                f"Invalid sensor update from {node_id} for {sensor}: {payload!r}"
            )

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

            log.info(
                f"Sensor update {node_id}/{sensor} = {value}"
            )

            table_dirty = True

    publish_status(client)
    print_table()

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

                publish_status(client)

        print_table()

# =========================
# FastAPI endpoints
# =========================
@app.get("/nodes")
def get_nodes():
    return {
        node_id: {
            "firmware": node.get("firmware"),
            "uptime": node.get("uptime"),
            "last_seen": now() - node.get("last_seen", now()),
            "sensors": node.get("sensors")
        }
        for node_id, node in nodes.items()
    }

@app.get("/nodes/{node_id}")
def get_node(node_id: str):

    node = nodes.get(node_id)

    if not node:
        return {"error": "node not found"}

    return {
        "firmware": node.get("firmware"),
        "uptime": node.get("uptime"),
        "last_seen": now() - node.get("last_seen", now()),
        "sensors": node.get("sensors")
    }

# =========================
#async def broadcast_update(data):
#    for ws in list(websockets):
#        try:
#            await ws.send_json(data)
#        except:
#            websockets.remove(ws)

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()          # Accept the WebSocket handshake
    connections.add(ws)
    try:
        while True:
            # keep connection alive
            await asyncio.sleep(10)
    except Exception as e:
        print("WebSocket closed:", e)
    finally:
        connections.remove(ws)

# =========================
# Start API
# =========================
def start_api():

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="warning",
        loop="asyncio"
    )

# =========================
# Main
# =========================
log.info("Starting SWAN Node Manager")

threading.Thread(target=start_api, daemon=True).start()

client = mqtt.Client()

if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)

client.on_connect = on_connect
client.on_message = on_message

log.info(f"Connecting to MQTT broker {MQTT_BROKER}:{MQTT_PORT}")

client.connect(MQTT_BROKER, MQTT_PORT)

threading.Thread(target=monitor_nodes, daemon=True).start()

client.loop_forever()