import os
import json
import time
import uuid
import threading
import logging
import asyncio
import shutil
import subprocess

import paho.mqtt.client as mqtt
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

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
# MQTT config
# =========================
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")

STATUS_TOPIC = "swan-hub/nodes/status"
HEARTBEAT_TIMEOUT = 30
WIFI_INTERFACE = os.getenv("WIFI_INTERFACE", "wlan0")

# =========================
# State
# =========================
nodes = {}
connections = set()
loop = None
mqtt_client = None
pending_sync_reads = {}
# pending_sync_reads[node_id] = [future, ...]
pending_sync_reads_lock = threading.Lock()
SYNC_READ_TIMEOUT = 10


def now():
    return int(time.time())


def get_wifi_tsf(interface=WIFI_INTERFACE):
    """Read the current Wi-Fi TSF timestamp from the Linux wireless driver."""
    iw_cmd = os.getenv("IW_CMD")
    if iw_cmd:
        iw_path = iw_cmd
    else:
        iw_path = shutil.which("iw")
        if not iw_path:
            for candidate in ["/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw", "/bin/iw"]:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    iw_path = candidate
                    break

    if not iw_path:
        raise RuntimeError("`iw` is not installed or not available in PATH")

    log.info(f"Using iw command at: {iw_path}")

    result = subprocess.run(
        [iw_path, "dev", interface, "station", "dump"],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to read TSF from interface {interface}: {result.stderr.strip() or result.stdout.strip()}"
        )

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("current time:"):
            parts = stripped.split()
            if len(parts) >= 3 and parts[-1] == "ms":
                try:
                    return int(parts[-2])
                except ValueError:
                    break

    raise RuntimeError("Unable to parse Wi-Fi TSF from iw output")


# =========================
# WebSocket broadcast
# =========================
async def broadcast(data):

    msg = json.dumps(data)

    for ws in list(connections):
        try:
            await ws.send_text(msg)
        except:
            connections.remove(ws)


def push(data):

    if loop:
        asyncio.run_coroutine_threadsafe(
            broadcast(data),
            loop
        )


def publish_mqtt_topic(topic, payload):
    if mqtt_client is None:
        raise RuntimeError("MQTT client is not initialized")

    mqtt_client.publish(topic, json.dumps(payload))


async def send_sync_read(node_id, payload=None, timeout=SYNC_READ_TIMEOUT):
    if loop is None:
        raise RuntimeError("Asyncio loop is not initialized")

    if mqtt_client is None:
        raise HTTPException(status_code=503, detail="MQTT client not available")

    if payload is None:
        payload = {"tsf_scheduled": 100}
    elif not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")
    elif "tsf_scheduled" not in payload:
        payload["tsf_scheduled"] = 100

    node = nodes.get(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")

    if now() - node.get("last_seen", 0) > HEARTBEAT_TIMEOUT:
        raise HTTPException(status_code=503, detail="node is offline")

    topic = f"swan-hub/command/{node_id}/sync-read"

    future = loop.create_future()

    with pending_sync_reads_lock:
        queue = pending_sync_reads.setdefault(node_id, [])
        queue.append(future)
        log.info(
            f"sync_read request queued for {node_id}; queue_length={len(queue)}; topic={topic}; payload={payload}"
        )

    publish_mqtt_topic(topic, payload)
    log.info(f"sync_read published to {topic} for {node_id}")

    try:
        data = await asyncio.wait_for(future, timeout)
        log.info(f"sync_read response received for {node_id}: {data}")
        return data
    except asyncio.TimeoutError:
        log.warning(f"sync_read timeout for {node_id} after {timeout}s; payload={payload}")
        raise HTTPException(status_code=504, detail="sync_read request timed out")
    finally:
        with pending_sync_reads_lock:
            queue = pending_sync_reads.get(node_id)
            if queue is not None and future in queue:
                queue.remove(future)
                if not queue:
                    pending_sync_reads.pop(node_id, None)


# =========================
# Node status payload
# =========================
def node_payload():

    return {
        node_id: {
            "firmware": node.get("firmware"),
            "uptime": node.get("uptime"),
            "last_seen": now() - node.get("last_seen", now()),
            "sensors": node.get("sensors")
        }
        for node_id, node in nodes.items()
    }


# =========================
# Heartbeat validation
# =========================
def validate_heartbeat(data):
    """
    Validate heartbeat structure and return (is_valid, error_message).
    
    Expected structure:
    {
        "firmware": str,
        "uptime": int,
        "sensors": [str, ...],
        "sensor_data": {sensor_name: value, ...}
    }
    """
    
    if not isinstance(data, dict):
        return False, "Payload is not a dictionary"
    
    # Check required fields
    required_fields = ["firmware", "uptime", "sensors", "sensor_data"]
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: {field}"
    
    # Validate field types
    if not isinstance(data["firmware"], str):
        return False, "firmware must be a string"
    
    if not isinstance(data["uptime"], int):
        return False, "uptime must be an integer"
    
    if not isinstance(data["sensors"], list):
        return False, "sensors must be a list"
    
    if not isinstance(data["sensor_data"], dict):
        return False, "sensor_data must be a dictionary"
    
    # Validate sensors list
    if not all(isinstance(s, str) for s in data["sensors"]):
        return False, "All sensors must be strings"
    
    # Validate sensor_data keys match sensors list
    sensor_names = set(data["sensors"])
    sensor_data_keys = set(data["sensor_data"].keys())
    
    if sensor_names != sensor_data_keys:
        missing = sensor_names - sensor_data_keys
        extra = sensor_data_keys - sensor_names
        msg = ""
        if missing:
            msg += f"Missing sensor data: {missing}. "
        if extra:
            msg += f"Extra sensor data: {extra}."
        return False, msg.strip()
    
    return True, None


# =========================
# MQTT callbacks
# =========================
def on_connect(client, userdata, flags, rc):

    if rc == 0:

        log.info("Connected to MQTT broker")

        client.subscribe("swan-hub/node/+/heartbeat")
        client.subscribe("swan-hub/node/+/+")

    else:
        log.error(f"MQTT connection failed: {rc}")


def on_message(client, userdata, msg):

    topic = msg.topic.split("/")
    payload = msg.payload.decode()

    if len(topic) < 4:
        return

    node_id = topic[2]
    subtopic = topic[3]

    # --------------------
    # SYNC READ RESPONSE
    # --------------------
    if subtopic == "sync_data":
        try:
            response = json.loads(payload)
        except json.JSONDecodeError:
            log.warning(f"Invalid JSON sync_data response from {node_id}: {payload!r}")
            return

        with pending_sync_reads_lock:
            queue = pending_sync_reads.get(node_id)
            future = queue.pop(0) if queue else None
            if queue is not None and not queue:
                pending_sync_reads.pop(node_id, None)

        if future is None:
            log.warning(
                f"Unexpected sync_data response from {node_id}; payload={response}"
            )
            return

        log.info(f"sync_data received for {node_id}; queue_remaining={len(queue) if queue else 0}")
        loop.call_soon_threadsafe(future.set_result, response)
        return

    # --------------------
    # HEARTBEAT
    # --------------------
    if subtopic == "heartbeat":

        try:

            data = json.loads(payload)
            
            # Validate heartbeat structure
            is_valid, error_msg = validate_heartbeat(data)
            if not is_valid:
                log.warning(f"Invalid heartbeat {node_id}: {error_msg}")
                return

            # Extract and store heartbeat data with sensor values
            nodes[node_id] = {
                "last_seen": now(),
                "firmware": data.get("firmware"),
                "uptime": data.get("uptime"),
                "sensors": data.get("sensor_data", {})
            }

            log.info(
                f"Heartbeat {node_id} │ fw={data.get('firmware')} │ sensors={data.get('sensors')}"
            )

        except json.JSONDecodeError:

            log.warning(f"Invalid JSON heartbeat {node_id}")
            return

    # --------------------
    # SENSOR UPDATE
    # --------------------
    else:

        sensor = subtopic

        try:
            value = json.loads(payload).get("value")
        except:
            value = payload

        node = nodes.setdefault(node_id, {
            "last_seen": now(),
            "firmware": None,
            "uptime": None,
            "sensors": {}
        })

        node["last_seen"] = now()
        node["sensors"][sensor] = value

        log.info(f"{node_id}/{sensor} = {value}")

    payload = node_payload()

    client.publish(
        STATUS_TOPIC,
        json.dumps(payload),
        retain=True
    )

    push(payload)


# =========================
# MQTT worker
# =========================
def mqtt_worker():

    global mqtt_client

    client = mqtt.Client()
    mqtt_client = client

    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    client.on_connect = on_connect
    client.on_message = on_message

    log.info(f"Connecting MQTT {MQTT_BROKER}:{MQTT_PORT}")

    client.connect(MQTT_BROKER, MQTT_PORT)

    client.loop_forever()


# =========================
# Offline detection
# =========================
def monitor_nodes():

    while True:

        time.sleep(5)

        for node_id in list(nodes):

            if now() - nodes[node_id]["last_seen"] > HEARTBEAT_TIMEOUT:

                log.warning(f"{node_id} offline")

                del nodes[node_id]

                push(node_payload())


# =========================
# FastAPI lifespan
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):

    global loop

    loop = asyncio.get_running_loop()

    log.info("Starting background workers")

    threading.Thread(target=mqtt_worker, daemon=True).start()
    threading.Thread(target=monitor_nodes, daemon=True).start()

    yield


# =========================
# FastAPI app
# =========================
app = FastAPI(
    title="SWAN Node Manager API",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# REST API
# =========================
@app.get("/nodes")
def get_nodes():
    return node_payload()


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


@app.get("/wifi/tsf")
def get_wifi_tsf_route():
    """Return the current Wi-Fi TSF timestamp from the Raspberry Pi."""
    try:
        tsf = get_wifi_tsf()
        return {"tsf": tsf, "interface": WIFI_INTERFACE}
    except Exception as err:
        raise HTTPException(status_code=500, detail=str(err))


@app.get("/nodes/{node_id}/sync-read")
async def sync_read_node_get(node_id: str, timeout: int = SYNC_READ_TIMEOUT):
    """Send a sync_read command immediately as a read-asap request."""

    data = await send_sync_read(node_id, timeout=timeout)
    return {
        "node_id": node_id,
        "sync_data": data
    }


# =========================
# WebSocket
# =========================
@app.websocket("/ws")
async def websocket(ws: WebSocket):

    await ws.accept()
    connections.add(ws)

    try:
        while True:
            await asyncio.sleep(10)
    finally:
        connections.remove(ws)