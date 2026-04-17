#!/bin/sh
# Startup script for DIVA — Data Intelligence Virtual Assistant
# Sets up the environment and runs the FastAPI application

set -e  # Exit on any error

# ── Color codes ─────────────────────────────────────────────────────────────

RESET='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[0;37m'
BG_BLUE='\033[44m'

# ── Print helpers ───────────────────────────────────────────────────────────

print_banner() {
    printf "\n"
    printf "${CYAN}${BOLD}\n"
    printf "  ╔═══════════════════════════════════════════════════════════╗\n"
    printf "  ║                                                           ║\n"
    printf "  ║        🤖  DIVA  🚀                                       ║\n"
    printf "  ║                                                           ║\n"
    printf "  ║        Data Intelligence Virtual Assistant                ║\n"
    printf "  ║        Enterprise Multi-Agent Chat System                 ║\n"
    printf "  ║                                                           ║\n"
    printf "  ╚═══════════════════════════════════════════════════════════╝\n"
    printf "${RESET}\n"
}

print_info() {
    printf "${BLUE}ℹ${RESET} $1\n"
}

print_success() {
    printf "${GREEN}✔${RESET} $1\n"
}

print_warning() {
    printf "${YELLOW}⚠${RESET} $1\n"
}

print_error() {
    printf "${RED}✖${RESET} $1\n"
}

print_step() {
    printf "${MAGENTA}▸${RESET} ${BOLD}$1${RESET}\n"
}

# Print banner
print_banner

# ── System information ─────────────────────────────────────────────────────

print_step "System Information"
print_info "Python: ${GREEN}$(python --version 2>&1)${RESET}"
print_info "Working Directory: ${CYAN}$(pwd)${RESET}"
print_info "User: ${CYAN}$(whoami)${RESET}"
printf "\n"

# ── Locate diva package ────────────────────────────────────────────────────

find_pythonpath() {
    if [ -d "/src/diva" ]; then
        echo "/src"
    elif [ -d "/workspace/src/diva" ]; then
        echo "/workspace/src"
    elif [ -d "/app/src/diva" ]; then
        echo "/app/src"
    elif [ -d "$(pwd)/src/diva" ]; then
        echo "$(pwd)/src"
    elif [ -d "/diva" ]; then
        echo "/"
    else
        echo ""
    fi
}

# ── Environment setup ──────────────────────────────────────────────────────

print_step "Environment Setup"
APP_PATH=$(find_pythonpath)

if [ -n "$APP_PATH" ]; then
    print_success "Located package: ${CYAN}${APP_PATH}/diva${RESET}"

    if [ -n "$PYTHONPATH" ]; then
        export PYTHONPATH="${APP_PATH}:${PYTHONPATH}"
        print_info "PYTHONPATH updated (appended to existing)"
    else
        export PYTHONPATH="${APP_PATH}"
        print_info "PYTHONPATH initialized"
    fi
    print_info "PYTHONPATH: ${DIM}${PYTHONPATH}${RESET}"
else
    print_warning "Could not locate diva in standard paths"
    print_info "Checking directory structure..."
    ls -la /
    [ -d "/src" ] && ls -la /src/ || print_warning "No /src directory"
    [ -d "/workspace" ] && ls -la /workspace/ || print_warning "No /workspace directory"
    print_warning "Attempting to continue anyway..."
fi

printf "\n"

# ── Activate venv if available ─────────────────────────────────────────────

if [ -f "./.venv/bin/activate" ]; then
    . "./.venv/bin/activate"
    print_info "Activated virtualenv: .venv"
elif [ -f "./venv/bin/activate" ]; then
    . "./venv/bin/activate"
    print_info "Activated virtualenv: venv"
fi

# ── Module verification ────────────────────────────────────────────────────

print_step "Module Verification"
python -c "import diva; print('\033[0;32m✔\033[0m Module import successful')" || {
    print_error "Module import failed!"
    print_error "PYTHONPATH: ${PYTHONPATH}"
    printf "\n"
    exit 1
}

MODULE_PATH=$(python -c "import diva; print(diva.__file__)" 2>/dev/null)
print_info "Module location: ${DIM}${MODULE_PATH}${RESET}"

# ── Server config ──────────────────────────────────────────────────────────

DIVA_HOST="${DIVA_HOST:-0.0.0.0}"
DIVA_PORT="${DIVA_PORT:-8000}"

printf "\n"
print_success "Pre-flight checks complete!"
print_info "Host: ${CYAN}${DIVA_HOST}${RESET}"
print_info "Port: ${CYAN}${DIVA_PORT}${RESET}"
printf "\n"
printf "${BG_BLUE}${WHITE}${BOLD}  🚀 LAUNCHING DIVA  ${RESET}\n"
printf "\n"

# ── Start the application ──────────────────────────────────────────────────

# Pass through any extra args (e.g., --reload, --workers 4)
exec python -m uvicorn diva.main:app --host "${DIVA_HOST}" --port "${DIVA_PORT}" "$@"
