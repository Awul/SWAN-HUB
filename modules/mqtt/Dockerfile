FROM eclipse-mosquitto:2.0

# Copy custom configuration
COPY config/mosquitto.conf /mosquitto/config/mosquitto.conf

# Create data and log directories
# currently no data persistance is enabled in the config, so the data folder will be unused
RUN mkdir -p /mosquitto/data /mosquitto/log

VOLUME ["/mosquitto/data", "/mosquitto/log"]
