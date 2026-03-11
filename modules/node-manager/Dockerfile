# SWAN-HUB/modules/node-manager/Dockerfile
FROM python:3.12-slim

# Set workdir
WORKDIR /app

# Copy Python requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Node Manager script
COPY node_manager.py .

# Use environment variables from .env
ENV MQTT_BROKER=${MQTT_BROKER}
ENV MQTT_PORT=${MQTT_PORT}
ENV MQTT_USER=${MQTT_USER}
ENV MQTT_PASS=${MQTT_PASS}

# Run the Node Manager
CMD ["python", "node_manager.py"]
