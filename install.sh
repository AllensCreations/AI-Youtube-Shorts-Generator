#!/usr/bin/env bash
# AI-Youtube-Shorts-Generator Installer
# Can be run locally or via curl:
#   curl -sSL https://raw.githubusercontent.com/SamurAIGPT/AI-Youtube-Shorts-Generator/main/install.sh | bash

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
CYAN="\033[0;36m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

echo -e "${CYAN}${BOLD}"
echo "==================================================================="
echo "  🎬 AI YouTube Shorts Generator & Admin Studio Installer"
echo "==================================================================="
echo -e "${RESET}"

# 1. Determine target directory
if [ -f "$PWD/server.py" ] && [ -f "$PWD/main.py" ]; then
    INSTALL_DIR="$PWD"
    echo -e "${GREEN}✓ Detected existing repository at:${RESET} $INSTALL_DIR"
elif [ -d "/root/AI-Youtube-Shorts-Generator" ]; then
    INSTALL_DIR="/root/AI-Youtube-Shorts-Generator"
    echo -e "${GREEN}✓ Using directory:${RESET} $INSTALL_DIR"
elif [ -d "$HOME/AI-Youtube-Shorts-Generator" ]; then
    INSTALL_DIR="$HOME/AI-Youtube-Shorts-Generator"
    echo -e "${GREEN}✓ Using directory:${RESET} $INSTALL_DIR"
else
    INSTALL_DIR="${INSTALL_DIR:-$HOME/AI-Youtube-Shorts-Generator}"
    echo -e "${YELLOW}Cloning repository to:${RESET} $INSTALL_DIR"
    git clone https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# 2. System dependencies (ffmpeg, python3-venv, git)
echo -e "\n${BOLD}[1/4] Checking system prerequisites (ffmpeg, python3)...${RESET}"
install_pkg() {
    if command -v apt-get >/dev/null 2>&1; then
        if [ "$EUID" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
            sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl
        else
            apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl
        fi
    elif command -v dnf >/dev/null 2>&1; then
        [ "$EUID" -ne 0 ] && SUDO="sudo" || SUDO=""
        $SUDO dnf install -y python3 python3-pip ffmpeg git curl
    elif command -v pacman >/dev/null 2>&1; then
        [ "$EUID" -ne 0 ] && SUDO="sudo" || SUDO=""
        $SUDO pacman -Sy --noconfirm python python-pip ffmpeg git curl
    elif command -v brew >/dev/null 2>&1; then
        brew install python ffmpeg git curl
    fi
}

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo -e "${YELLOW}Installing missing system packages...${RESET}"
    install_pkg || true
else
    echo -e "${GREEN}✓ System packages already installed.${RESET}"
fi

# 3. Python virtual environment & packages
echo -e "\n${BOLD}[2/4] Setting up Python virtual environment...${RESET}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi

VENV_PIP="$INSTALL_DIR/venv/bin/pip"
VENV_PYTHON="$INSTALL_DIR/venv/bin/python3"

echo -e "${CYAN}Installing dependencies in venv...${RESET}"
"$VENV_PIP" install -q --upgrade pip
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$VENV_PIP" install -q -r "$INSTALL_DIR/requirements.txt"
fi
if [ -f "$INSTALL_DIR/requirements-local.txt" ]; then
    "$VENV_PIP" install -q -r "$INSTALL_DIR/requirements-local.txt"
fi
echo -e "${GREEN}✓ Python packages installed successfully.${RESET}"

# 4. Configuration .env
echo -e "\n${BOLD}[3/4] Configuring environment (.env)...${RESET}"
if [ ! -f "$INSTALL_DIR/.env" ] && [ -f "$INSTALL_DIR/.env.example" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo -e "${GREEN}✓ Created .env from .env.example${RESET}"
else
    echo -e "${GREEN}✓ .env already configured.${RESET}"
fi

mkdir -p "$INSTALL_DIR/output/jobs"

# 5. Install global 'AI' CLI command
echo -e "\n${BOLD}[4/4] Installing global 'AI' command...${RESET}"
chmod +x "$INSTALL_DIR/AI"

# Create launcher wrapper pointing to this install directory
LAUNCHER_CONTENT="#!/usr/bin/env bash
exec \"$INSTALL_DIR/AI\" \"\$@\"
"

INSTALLED_PATH=""
if [ -w "/usr/local/bin" ] || [ "$EUID" -eq 0 ]; then
    echo "$LAUNCHER_CONTENT" > /usr/local/bin/AI
    chmod +x /usr/local/bin/AI
    INSTALLED_PATH="/usr/local/bin/AI"
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    echo "$LAUNCHER_CONTENT" | sudo tee /usr/local/bin/AI > /dev/null
    sudo chmod +x /usr/local/bin/AI
    INSTALLED_PATH="/usr/local/bin/AI"
else
    mkdir -p "$HOME/.local/bin"
    echo "$LAUNCHER_CONTENT" > "$HOME/.local/bin/AI"
    chmod +x "$HOME/.local/bin/AI"
    INSTALLED_PATH="$HOME/.local/bin/AI"

    # Ensure ~/.local/bin is in PATH
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        export PATH="$HOME/.local/bin:$PATH"
        for RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
            if [ -f "$RC" ]; then
                echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
            fi
        done
    fi
fi

echo -e "${GREEN}✓ Installed global 'AI' command at: ${BOLD}$INSTALLED_PATH${RESET}"

echo -e "\n${GREEN}${BOLD}==================================================================="
echo "  🎉 Installation Complete!"
echo "===================================================================${RESET}"
echo -e "You can now run:"
echo -e "  ${CYAN}${BOLD}AI --run${RESET}          -> Starts the Admin Overview on ${BOLD}http://localhost:5000${RESET}"
echo -e "  ${CYAN}${BOLD}AI --run --port 8080${RESET}  -> Starts on a custom port"
echo -e "  ${CYAN}${BOLD}AI \"<youtube_url>\"${RESET}  -> Runs clipping directly via CLI"
echo -e "${GREEN}===================================================================${RESET}\n"
