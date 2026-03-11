#!/bin/bash


# Set MQTT password for SWAN-HUB

# Absolute path to repo root (one level up from utility/)
REPO_ROOT="$(cd "$(dirname "$0")"/.. && pwd)"
PASSWD_FILE="$REPO_ROOT/modules/mqtt/config/passwd_file"
MQTT_USER="swan"

# super colors (ANSI)

GREEN="\033[1;32m"
YELLOW="\033[1;33m"
CYAN="\033[1;36m"
RESET="\033[0m"


# Source misc utilities
source "$REPO_ROOT/utility/misc/printihta.sh"
source "$REPO_ROOT/utility/misc/printline.sh"

# Header
printline 36
printihta
printline 36
echo -e "${GREEN}SWAN-HUB MQTT Password Setup${RESET}"
printline 36
echo

# Ensure mqtt config folder exists
mkdir -p "$(dirname "$PASSWD_FILE")"

# Prompt for password
read -sp "$(echo -e ${YELLOW}Enter MQTT password to set for user ${MQTT_USER}: ${RESET})" MQTT_PASS
echo -e "\n"

# Remove old password file if it exists
if [ -f "$PASSWD_FILE" ]; then
    echo -e "${YELLOW}Removing old Mosquitto password file... ($PASSWD_FILE)${RESET}"
    sudo rm -f "$PASSWD_FILE"
fi

# Run mosquitto_passwd in Docker to create hashed password
docker run -it --rm \
    -it -v "$REPO_ROOT/modules/mqtt/config":/mosquitto/config \
    eclipse-mosquitto:2.0 mosquitto_passwd -b -c /mosquitto/config/passwd_file "$MQTT_USER" "$MQTT_PASS"

# Success message
echo -e "${GREEN}Mosquitto password file created/updated at ${PASSWD_FILE}${RESET}"
# it works now
# echo -e "${YELLOW} PLEASE update your .env file accordingly. Yes i know it can be automatized. Why don't you create a pull request?${RESET}"

$REPO_ROOT/utility/setupenv.sh $MQTT_PASS
