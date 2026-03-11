import time
import json
import threading
import os
import paho.mqtt.client as mqtt

# Read config from environment variables
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "default_user")
MQTT_PASS = os.getenv("MQTT_PASS", "default_pass")

OVERVIEW_TOPIC = "nodes/overview"
NODE_TIMEOUT = 60  # seconds

nodes = {}

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    print("Connected to MQTT broker")
    client.subscribe("nodes/+/status")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        node_id = msg.topic.split("/")[1]
        nodes[node_id] = {
            "sensors": data.get("sensors", []),
            "last_seen": time.time()
        }
        publish_overview()
    except Exception as e:
        print(f"Error processing message: {e}")

def publish_overview():
    client.publish(OVERVIEW_TOPIC, json.dumps(nodes), retain=True)

def cleanup_stale_nodes():
    while True:
        now = time.time()
        removed = False
        for node_id in list(nodes.keys()):
            if now - nodes[node_id]["last_seen"] > NODE_TIMEOUT:
                print(f"Removing stale node {node_id}")
                del nodes[node_id]
                removed = True
        if removed:
            publish_overview()
        time.sleep(5)

client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_BROKER, MQTT_PORT, 60)
client.loop_start()

threading.Thread(target=cleanup_stale_nodes, daemon=True).start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Shutting down node handler")
    client.loop_stop()
