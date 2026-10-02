#!/usr/bin/env bash
# ============================================================
# OpenFotoFrame - One-Command Setup
# ============================================================
#
# Usage: ./scripts/install.sh
#
# What this script does (depending on selected mode):
#
#   1) Backend + display — full setup: Docker, HTTPS, app, Chromium kiosk
#   2) Backend only      — Docker, HTTPS, app (no local display)
#   3) Display only      — Chromium kiosk pointing at a remote backend

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INITIAL_ADMIN_PASSWORD=""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Detect the correct Chromium binary/package name for this distro
detect_chromium() {
    if command -v chromium-browser &>/dev/null; then
        echo "chromium-browser"
    elif command -v chromium &>/dev/null; then
        echo "chromium"
    else
        # Not installed yet — check which package is available
        if apt-cache show chromium-browser &>/dev/null 2>&1; then
            echo "chromium-browser"
        else
            echo "chromium"
        fi
    fi
}

# ---------- Docker ----------

install_docker() {
    if command -v docker &>/dev/null; then
        info "Docker already installed: $(docker --version)"
        return 0
    fi

    info "Installing Docker..."
    curl -fsSL https://get.docker.com | sh

    info "Adding $USER to the docker group..."
    sudo usermod -aG docker "$USER"

    warn "You were added to the docker group."
    warn "If this is a fresh install, you may need to log out and back in."
}

enable_docker_on_boot() {
    info "Enabling Docker to start on boot..."
    sudo systemctl enable docker
    sudo systemctl start docker
}

# Allow Docker containers to bind to privileged ports (80, 443)
allow_privileged_ports() {
    local sysctl_key="net.ipv4.ip_unprivileged_port_start"
    local current
    current=$(sysctl -n "$sysctl_key" 2>/dev/null || echo "1024")

    if [ "$current" -le 80 ]; then
        info "Privileged port binding already allowed (start=$current)"
        return 0
    fi

    info "Allowing containers to bind to ports 80/443..."
    echo "$sysctl_key=80" | sudo tee /etc/sysctl.d/allow-privileged-ports.conf > /dev/null
    sudo sysctl -p /etc/sysctl.d/allow-privileged-ports.conf > /dev/null
}

# ---------- HTTPS / TLS ----------

CONFIGURED_DOMAIN=""

setup_https() {
    # If a Caddyfile already exists, offer to keep it
    if [ -f "$PROJECT_DIR/Caddyfile" ]; then
        echo ""
        info "Existing Caddyfile found."
        read -rp "Keep current HTTPS configuration? [Y/n]: " keep_https
        if [[ ! "$keep_https" =~ ^[Nn]$ ]]; then
            info "Keeping existing HTTPS configuration."
            return 0
        fi
    fi

    echo ""
    echo "  HTTPS mode:"
    echo "    1) Self-signed certificate (default — works immediately, browser warning)"
    echo "    2) Let's Encrypt via Cloudflare (trusted cert, requires domain + API token)"
    echo "    3) Let's Encrypt via DuckDNS   (trusted cert, free subdomain)"
    echo ""
    read -rp "Choose HTTPS mode [1/2/3]: " https_mode

    case "$https_mode" in
        2) setup_letsencrypt_cloudflare ;;
        3) setup_letsencrypt_duckdns ;;
        *) info "Using self-signed certificates (default)." ;;
    esac
}

setup_letsencrypt_cloudflare() {
    echo ""
    read -rp "  Domain name (e.g. photos.example.com): " domain
    if [[ -z "$domain" ]]; then
        warn "No domain entered. Falling back to self-signed."
        return 0
    fi

    read -rp "  Cloudflare API token: " cf_token
    if [[ -z "$cf_token" ]]; then
        warn "No token entered. Falling back to self-signed."
        return 0
    fi

    echo ""
    echo "  Zone ID is on your domain's Cloudflare overview page (right sidebar, under API)."
    read -rp "  Cloudflare Zone ID: " cf_zone_id
    if [[ -z "$cf_zone_id" ]]; then
        warn "No Zone ID entered. DDNS auto-update will not be available."
    fi

    # Write .env
    cat > "$PROJECT_DIR/.env" <<EOF
DOMAIN=$domain
CLOUDFLARE_API_TOKEN=$cf_token
${cf_zone_id:+CLOUDFLARE_ZONE_ID=$cf_zone_id}
EOF
    info "Saved Cloudflare credentials to .env"

    # Write Caddyfile for Let's Encrypt + any-hostname fallback
    cat > "$PROJECT_DIR/Caddyfile" <<EOF
$domain {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy photo-frame:5000
}

localhost {
    tls internal
    reverse_proxy photo-frame:5000
}

http:// {
    redir https://{host}{uri} permanent
}
EOF
    info "Caddyfile configured for Let's Encrypt (Cloudflare DNS)."
    CONFIGURED_DOMAIN="$domain"
}

setup_letsencrypt_duckdns() {
    echo ""
    read -rp "  DuckDNS subdomain (e.g. myframe — becomes myframe.duckdns.org): " subdomain
    if [[ -z "$subdomain" ]]; then
        warn "No subdomain entered. Falling back to self-signed."
        return 0
    fi

    local domain="${subdomain}.duckdns.org"

    read -rp "  DuckDNS token: " duck_token
    if [[ -z "$duck_token" ]]; then
        warn "No token entered. Falling back to self-signed."
        return 0
    fi

    # Write .env
    cat > "$PROJECT_DIR/.env" <<EOF
DOMAIN=$domain
DUCKDNS_TOKEN=$duck_token
EOF
    info "Saved DuckDNS credentials to .env"

    # Write Caddyfile for Let's Encrypt + any-hostname fallback
    cat > "$PROJECT_DIR/Caddyfile" <<EOF
$domain {
    tls {
        dns duckdns {env.DUCKDNS_TOKEN}
    }
    reverse_proxy photo-frame:5000
}

localhost {
    tls internal
    reverse_proxy photo-frame:5000
}

http:// {
    redir https://{host}{uri} permanent
}
EOF
    info "Caddyfile configured for Let's Encrypt (DuckDNS)."
    CONFIGURED_DOMAIN="$domain"
}

# ---------- Build & start ----------

start_services() {
    info "Building and starting services (this may take a while on first run)..."
    cd "$PROJECT_DIR"
    docker compose up -d --build
    # The app creates a random one-time credential in its protected data volume.
    # Consume it locally and delete the plaintext file before showing it once below.
    for _ in {1..60}; do
        if docker exec openfotoframe test -f /app/data/.initial_admin_password 2>/dev/null; then
            INITIAL_ADMIN_PASSWORD=$(docker exec openfotoframe sh -c \
                'password=$(cat /app/data/.initial_admin_password) && rm /app/data/.initial_admin_password && printf %s "$password"')
            break
        fi
        if docker exec openfotoframe test -f /app/data/users.json 2>/dev/null; then
            break
        fi
        sleep 1
    done
    info "Services started."
}

# ---------- Kiosk mode (optional) ----------

setup_kiosk() {
    local chromium_pkg
    chromium_pkg=$(detect_chromium)
    info "Using Chromium package: $chromium_pkg"

    info "Installing kiosk packages..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq "$chromium_pkg" unclutter xdotool x11-xserver-utils

    # Re-detect after install to get the actual binary name
    local chromium_bin
    chromium_bin=$(detect_chromium)

    # Create kiosk start script
    info "Creating kiosk start script..."
    cat > "$PROJECT_DIR/start_kiosk.sh" <<KIOSKEOF
#!/bin/bash

CHROMIUM="$chromium_bin"

# Wait for the photo frame to be ready
echo "Waiting for server to start..."
for i in {1..60}; do
    if curl -sk https://localhost > /dev/null 2>&1; then
        echo "Server is ready!"
        break
    fi
    sleep 1
done

DISPLAY_URL="https://localhost/display"

# Disable screen blanking/power management
xset s off
xset s noblank
xset -dpms

# Hide mouse cursor
unclutter -idle 0.5 -root &

# Start Chromium in kiosk mode (restart automatically if it crashes)
while true; do
    \$CHROMIUM \\
        --kiosk \\
        --noerrdialogs \\
        --disable-infobars \\
        --disable-session-crashed-bubble \\
        --disable-translate \\
        --no-first-run \\
        --start-fullscreen \\
        --autoplay-policy=no-user-gesture-required \\
        --check-for-update-interval=31536000 \\
        --ignore-certificate-errors \\
        --password-store=basic \\
        "\$DISPLAY_URL"
    echo "Chromium exited, restarting in 3 seconds..."
    sleep 3
done
KIOSKEOF
    chmod +x "$PROJECT_DIR/start_kiosk.sh"

    # Create autostart entry
    mkdir -p ~/.config/autostart
    cat > ~/.config/autostart/photo-frame-kiosk.desktop <<EOF
[Desktop Entry]
Type=Application
Name=OpenFotoFrame Kiosk
Exec=$PROJECT_DIR/start_kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

    info "Kiosk mode configured. It will launch automatically after reboot."
}

# ---------- HDMI-CEC TV control (optional) ----------

setup_cec() {
    echo ""
    read -rp "Enable HDMI-CEC TV power control? [y/N]: " cec_answer
    if [[ ! "$cec_answer" =~ ^[Yy]$ ]]; then
        info "Skipping CEC setup."
        return 0
    fi

    # Check if CEC device exists
    if [ ! -e /dev/cec0 ]; then
        warn "No CEC device found at /dev/cec0."
        warn "Ensure your Pi is connected via HDMI and CEC is enabled."
        warn "You may need to add 'hdmi_force_hotplug=1' to /boot/config.txt and reboot."
        return 0
    fi

    info "Installing cec-utils on host (for testing)..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq cec-utils

    # Get the GID of the CEC device (usually 'video' group, GID 44)
    local cec_gid
    cec_gid=$(stat -c %g /dev/cec0 2>/dev/null || echo "44")
    info "CEC device group GID: $cec_gid"

    # Enable CEC device passthrough + group access in docker-compose.yml
    if [ -f "$PROJECT_DIR/docker-compose.yml" ]; then
        info "Enabling CEC device passthrough in docker-compose.yml..."
        sed -i 's/# devices:/devices:/' "$PROJECT_DIR/docker-compose.yml"
        sed -i 's/#   - "\/dev\/cec0:\/dev\/cec0"/  - "\/dev\/cec0:\/dev\/cec0"/' "$PROJECT_DIR/docker-compose.yml"
        sed -i "s/# group_add:/group_add:/" "$PROJECT_DIR/docker-compose.yml"
        sed -i "s/#   - \"44\"/  - \"$cec_gid\"/" "$PROJECT_DIR/docker-compose.yml"
    fi

    # Rebuild with CEC device
    info "Rebuilding container with CEC support..."
    cd "$PROJECT_DIR"
    docker compose up -d --build

    info "CEC TV control enabled. Configure schedules in the web UI."
}

# ---------- Tailscale (optional) ----------

setup_tailscale() {
    echo ""
    read -rp "Install Tailscale for secure remote access? [y/N]: " ts_answer
    if [[ ! "$ts_answer" =~ ^[Yy]$ ]]; then
        info "Skipping Tailscale setup."
        return 0
    fi

    info "Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh

    info "Starting Tailscale..."
    sudo tailscale up

    local ts_ip
    ts_ip=$(tailscale ip -4 2>/dev/null || echo "unknown")
    info "Tailscale IP: $ts_ip"
    info "Use this IP to access the photo frame remotely."
}

# ---------- Chromium restart cron ----------

setup_chromium_cron() {
    local cron_script="$PROJECT_DIR/scripts/restart-chromium.sh"
    local cron_entry="0 4 * * * $cron_script"

    if crontab -l 2>/dev/null | grep -qF "$cron_script"; then
        info "Chromium restart cron job already exists."
        return 0
    fi

    info "Adding daily Chromium restart cron job (4:00 AM)..."
    (crontab -l 2>/dev/null || true; echo "$cron_entry") | crontab -
    info "Cron job added."
}

# ---------- Cloudflare DDNS cron ----------

setup_ddns_cron() {
    # Only set up if Cloudflare Zone ID is configured
    if [[ ! -f "$PROJECT_DIR/.env" ]] || ! grep -q "CLOUDFLARE_ZONE_ID" "$PROJECT_DIR/.env"; then
        return 0
    fi

    local ddns_script="$PROJECT_DIR/scripts/cloudflare-ddns.sh"
    local cron_entry="0 */6 * * * $ddns_script"

    if crontab -l 2>/dev/null | grep -qF "$ddns_script"; then
        info "Cloudflare DDNS cron job already exists."
        return 0
    fi

    info "Adding Cloudflare DDNS cron job (every 6 hours)..."
    (crontab -l 2>/dev/null || true; echo "$cron_entry") | crontab -
    info "DDNS cron job added."

    # Run once now to create/update the record immediately
    info "Running initial DDNS update..."
    bash "$ddns_script" || warn "DDNS update failed — check .env credentials."
}

# ---------- Display-only mode ----------

setup_display_only() {
    echo ""
    echo "  Display-only setup: this device will run Chromium pointing at a remote backend."
    echo "  You need the backend URL. Chromium will always start with a clean /display URL."
    echo "  On first launch, enter the enrollment secret from the backend admin UI once."
    echo "  The browser stores only a protected display session cookie."
    echo ""

    local backend_url=""
    while [[ -z "$backend_url" ]]; do
        read -rp "  Backend URL (e.g. https://192.168.1.100 or https://photos.example.com): " backend_url
    done
    # Strip trailing slash
    backend_url="${backend_url%/}"

    # CEC TV power control
    local cec_enabled=false
    echo ""
    if [[ -e /dev/cec0 ]]; then
        read -rp "  CEC device detected (/dev/cec0). Enable TV power control via schedules? [Y/n]: " cec_answer
        if [[ ! "$cec_answer" =~ ^[Nn]$ ]]; then
            cec_enabled=true
        fi
    else
        read -rp "  No CEC device detected. Enable TV power control anyway? [y/N]: " cec_answer
        if [[ "$cec_answer" =~ ^[Yy]$ ]]; then
            cec_enabled=true
        fi
    fi

    local chromium_pkg
    chromium_pkg=$(detect_chromium)

    info "Installing kiosk packages..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq "$chromium_pkg" unclutter xdotool x11-xserver-utils curl

    if [[ "$cec_enabled" == true ]]; then
        info "Installing CEC utilities..."
        sudo apt-get install -y -qq cec-utils
    fi

    local chromium_bin
    chromium_bin=$(detect_chromium)

    # CEC receives a separate bearer credential; never reuse display enrollment.
    local cec_launch=""
    if [[ "$cec_enabled" == true ]]; then
        echo ""
        echo "  Get the CEC agent token from the backend administrator endpoint:"
        echo "    GET /api/cec/agent-token"
        local cec_agent_token=""
        while [[ -z "$cec_agent_token" ]]; do
            read -rsp "  CEC agent token: " cec_agent_token
            echo ""
        done
        sudo mkdir -p /etc/openfotoframe
        printf '%s\n' "$cec_agent_token" | sudo tee /etc/openfotoframe/cec-agent-token >/dev/null
        sudo chmod 0600 /etc/openfotoframe/cec-agent-token
        sudo chown "$USER:$(id -gn)" /etc/openfotoframe/cec-agent-token
        unset cec_agent_token
        cec_launch="# Start CEC agent for TV power control (runs in background)
\"$PROJECT_DIR/scripts/cec-agent.sh\" \"$backend_url\" &"
    fi

    info "Creating kiosk start script..."
    cat > "$PROJECT_DIR/start_kiosk.sh" <<KIOSKEOF
#!/bin/bash

CHROMIUM="$chromium_bin"
BACKEND_URL="$backend_url"
DISPLAY_URL="\${BACKEND_URL}/display"

# Wait for the backend to be reachable
echo "Waiting for backend server..."
for i in {1..120}; do
    if curl -sk "\${BACKEND_URL}" > /dev/null 2>&1; then
        echo "Backend is ready!"
        break
    fi
    sleep 1
done

# Disable screen blanking/power management
xset s off
xset s noblank
xset -dpms

# Hide mouse cursor
unclutter -idle 0.5 -root &

$cec_launch

# Start Chromium in kiosk mode (restart automatically if it crashes)
while true; do
    \$CHROMIUM \\
        --kiosk \\
        --noerrdialogs \\
        --disable-infobars \\
        --disable-session-crashed-bubble \\
        --disable-translate \\
        --no-first-run \\
        --start-fullscreen \\
        --autoplay-policy=no-user-gesture-required \\
        --check-for-update-interval=31536000 \\
        --ignore-certificate-errors \\
        --password-store=basic \\
        "\$DISPLAY_URL"
    echo "Chromium exited, restarting in 3 seconds..."
    sleep 3
done
KIOSKEOF
    chmod +x "$PROJECT_DIR/start_kiosk.sh"

    mkdir -p ~/.config/autostart
    cat > ~/.config/autostart/photo-frame-kiosk.desktop <<EOF
[Desktop Entry]
Type=Application
Name=OpenFotoFrame Kiosk
Exec=$PROJECT_DIR/start_kiosk.sh
X-GNOME-Autostart-enabled=true
EOF

    setup_chromium_cron

    echo ""
    echo "======================================"
    info "Display-only setup complete!"
    echo ""
    echo "  This device will open Chromium pointing at:"
    echo "    ${backend_url}/display"
    echo ""
    echo "  The kiosk will launch automatically on next login/reboot."
    echo "  To start it now: $PROJECT_DIR/start_kiosk.sh"
    echo "  First launch will prompt once for the display enrollment secret."
    echo ""
    if [[ "$cec_enabled" == true ]]; then
        echo "  CEC TV power control: enabled with a separate protected agent token."
        echo "  Configure schedules in the backend admin UI (Settings → TV Schedule)."
        echo "  Commands execute on this Pi within ~30 seconds of their scheduled time."
        echo ""
    fi
    echo "  Chromium restarts daily at 4:00 AM to prevent memory leaks."
    echo "======================================"
}

# ---------- Main ----------

main() {
    echo ""
    echo "======================================"
    echo "  OpenFotoFrame - Setup"
    echo "======================================"
    echo ""
    echo "  Setup mode:"
    echo "    1) Backend server + display  — full setup: app, HTTPS, and Chromium kiosk"
    echo "    2) Backend server only       — app + HTTPS, no local display"
    echo "    3) Display only              — Chromium kiosk pointing at a remote backend"
    echo ""
    read -rp "Choose mode [1/2/3, default 1]: " setup_mode
    echo ""

    case "$setup_mode" in
        3)
            setup_display_only
            return
            ;;
        2)
            install_docker
            enable_docker_on_boot
            allow_privileged_ports
            setup_https
            start_services
            setup_tailscale
            setup_ddns_cron
            ;;
        *)
            install_docker
            enable_docker_on_boot
            allow_privileged_ports
            setup_https
            start_services
            setup_kiosk
            setup_cec
            setup_tailscale
            setup_chromium_cron
            setup_ddns_cron
            ;;
    esac

    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')

    echo ""
    echo "======================================"
    info "Setup complete!"
    echo ""
    if [[ -n "$CONFIGURED_DOMAIN" ]]; then
        echo "  Upload photos at:"
        echo "    https://${CONFIGURED_DOMAIN}/upload"
        echo ""
        echo "  Display URL (for other screens):"
        echo "    https://${CONFIGURED_DOMAIN}/display"
        echo ""
        echo "  Trusted Let's Encrypt certificate — no browser warnings!"
        if grep -q "CLOUDFLARE_ZONE_ID" "$PROJECT_DIR/.env" 2>/dev/null; then
            echo "  DDNS auto-updates the A record every 6 hours."
        else
            echo "  (Make sure your DNS points ${CONFIGURED_DOMAIN} to ${ip})"
        fi
    else
        echo "  Upload photos at:"
        echo "    https://${ip}/upload"
        echo ""
        echo "  Display URL (for other screens):"
        echo "    https://${ip}/display"
        echo ""
        echo "  (Self-signed certificate — browser will show a warning)"
        echo "  To switch to trusted certs later, run: ./scripts/install.sh"
    fi
    echo ""
    if [[ -n "$INITIAL_ADMIN_PASSWORD" ]]; then
        echo "  One-time administrator login (shown only now):"
        echo "    Username: admin"
        echo "    Password: $INITIAL_ADMIN_PASSWORD"
        echo "  You must change this password on first login."
        unset INITIAL_ADMIN_PASSWORD
    else
        echo "  Administrator credentials already initialized."
        echo "  If this is a fresh manual deployment, consume the protected"
        echo "  /app/data/.initial_admin_password file locally before login."
    fi
    echo ""
    echo "  Useful commands:"
    echo "    View logs:  docker compose logs -f"
    echo "    Restart:    docker compose restart"
    echo "    Rebuild:    docker compose up -d --build"
    if [[ "$setup_mode" != "2" ]]; then
        echo ""
        echo "  Chromium restarts daily at 4:00 AM to prevent memory leaks."
    fi
    echo "======================================"
}

main "$@"
