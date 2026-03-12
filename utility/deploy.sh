# Absolute path to repo root
REPO_ROOT="$(cd "$(dirname "$0")"/.. && pwd)"
ENV_FILE="$REPO_ROOT/.env"

# Source misc utilities
source "$REPO_ROOT/utility/misc/printihta.sh"
source "$REPO_ROOT/utility/misc/printline.sh"

# Header
printline 36
printihta
printline 36
echo -e "${GREEN}Deploying SWAN-HUB${RESET}"
printline 36
echo

docker compose up -d --build
