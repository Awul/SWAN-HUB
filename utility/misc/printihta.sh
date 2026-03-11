#!/bin/bash
printihta() {    # must match the call in setup_env.sh
    RED="\033[1;31m"
    ORANGE="\033[38;5;208m"
    YELLOW="\033[1;33m"
    GREEN="\033[1;32m"
    CYAN="\033[1;36m"
    BLUE="\033[1;34m"
    MAGENTA="\033[1;35m"
    RESET="\033[0m"

    echo -e "${RED} __   __    __  .___________.    ___      ${RESET}"
    echo -e "${ORANGE}|  | |  |  |  | |           |   /   \\     ${RESET}"
    echo -e "${YELLOW}|  | |  |__|  | \`---|  |----\`  /  ^  \\    ${RESET}"
    echo -e "${GREEN}|  | |   __   |     |  |      /  /_\\  \\   ${RESET}"
    echo -e "${CYAN}|  | |  |  |  |     |  |     /  _____  \\  ${RESET}"
    echo -e "${BLUE}|__| |__|  |__|     |__|    /__/     \\__\\ ${RESET}"
    echo -e "${MAGENTA}                                          ${RESET}"
}
