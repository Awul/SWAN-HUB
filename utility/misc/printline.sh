#!/bin/bash
printline() {
    COLOR="${1:-36}"   # default cyan
    echo -e "\033[1;${COLOR}m==============================================\033[0m"
}
