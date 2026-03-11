#!/bin/bash

# Cool colors
YELLOW="\033[1;33m"
GREEN="\033[1;32m"
CYAN="\033[1;36m"
MAGENTA="\033[1;35m"
RESET="\033[0m"

# Absolute path to repo root
REPO_ROOT="$(cd "$(dirname "$0")"/.. && pwd)"
ENV_FILE="$REPO_ROOT/.env"

# Source misc utilities
source "$REPO_ROOT/utility/misc/printihta.sh"
source "$REPO_ROOT/utility/misc/printline.sh"

if [ -n "$1" ]; then
    # Use password from argument
    MQTT_PASS="$1"
    echo -e "${CYAN}Using password from argument (automatic mode)${RESET}"
else

    printline 34
    # Print logo and header lines
    printihta
    printline 34
    echo -e "${GREEN}SWAN-HUB Environment Setup${RESET}"
    printline 34

    # Instructions
    echo -e "${YELLOW}Dearest user! This script creates a .env file for MQTT credentials."
    echo -e "The original MQTT password is already set in${MAGENTA} /modules/mqtt/config/passwd_file${YELLOW} (hashed)."
    echo -e "If you want to change the password, please run${MAGENTA} /utility/setmqttpassword.sh${RESET}"
    echo

    # Interactive prompt
    read -sp "$(echo -e "${YELLOW}Enter default MQTT password: ${RESET}")" MQTT_PASS
    echo
fi

echo

# Create/overwrite .env
cat <<EOF > "$ENV_FILE"
# MQTT credentials
MQTT_USER=swan
MQTT_PASS=$MQTT_PASS

# Broker host/port
MQTT_BROKER=mqtt
MQTT_PORT=1883
EOF

echo -e "${CYAN}.env file created/updated at $ENV_FILE${RESET}"
printline 34
