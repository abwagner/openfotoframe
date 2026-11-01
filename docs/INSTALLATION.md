# Installation and networking

Start with the [quick install](../README.md#install). This guide covers installer options, HTTPS and network access.

## Installer setup

### What You Need

- Any Linux machine (Raspberry Pi, Ubuntu server, etc.) with a desktop environment if using kiosk mode
- Network connection
- HDMI cable to a TV/monitor (if using as a dedicated display)

### Step 1: Install

```bash
ssh user@your-host
git clone https://github.com/abwagner/openfotoframe.git
cd openfotoframe
./scripts/install.sh
```

Choose backend + display, backend only, or display only. Depending on the mode, the script will:

1. Install Docker and enable it to start on boot
2. Ask which HTTPS mode you want (self-signed, Cloudflare, or DuckDNS)
3. Build and start the photo frame app with Caddy (HTTPS)
4. Ask if you want Chromium kiosk mode for a connected display
5. Ask if you want HDMI-CEC TV power control
6. Ask if you want Tailscale for secure remote access
7. For a local kiosk, add a daily Chromium restart job (4:00 AM)

If you chose kiosk mode, reboot to start it:

```bash
sudo reboot
```

### Step 2: Upload Photos

From any device on your network (phone, laptop, etc.):

1. Open `https://<your-ip>/upload` (or `https://<your-domain>/upload` if using Let's Encrypt)
2. Log in as `admin` with the random one-time password printed by the installer
3. You'll be required to set a password of at least 12 characters before continuing
4. Upload photos — they appear on the display automatically

### Headless Operation

Once kiosk mode is set up, you never need to touch the machine again. Everything is managed through the web interface from your phone or laptop. It just needs power and an HDMI connection to your display.

## Trusted HTTPS (Let's Encrypt)

By default, the app uses a self-signed certificate (works immediately but shows a browser warning). For a trusted certificate with no warnings, the install script offers two DNS challenge options:

### Option 1: Cloudflare DNS

Best if you already own a domain managed by Cloudflare.

1. During install, choose **Let's Encrypt via Cloudflare**
2. Enter your domain (e.g., `photos.example.com`)
3. Enter your Cloudflare API token
4. Enter your Cloudflare Zone ID (enables automatic DDNS updates)

**Getting a Cloudflare API token:**
1. Go to [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens)
2. Click **Create Token** → use the **Edit zone DNS** template
3. Configure the token:
   - **Permissions**: Zone / DNS / Edit (pre-filled by the template)
   - **Zone Resources**: Include → Specific zone → select your domain
   - **Client IP Address Filtering**: Leave blank (optional — restrict which IPs can use the token)
   - **TTL**: Leave blank for no expiration, or set an end date if you prefer rotating tokens

**Zone ID** is on your domain's Cloudflare overview page (right sidebar, under "API").

If you provide the Zone ID, the install script sets up a DDNS cron job that runs every 6 hours. It checks your public IP and automatically creates or updates the A record in Cloudflare — no need to manually manage DNS when your IP changes. Caddy will automatically obtain and renew the certificate.

### Option 2: DuckDNS (Free)

Best if you don't own a domain. DuckDNS provides a free `yourname.duckdns.org` subdomain.

1. During install, choose **Let's Encrypt via DuckDNS**
2. Enter your DuckDNS subdomain (e.g., `myframe`)
3. Enter your DuckDNS token

**Getting a DuckDNS token:**
1. Go to [duckdns.org](https://www.duckdns.org) and sign in
2. Create a subdomain
3. Copy your token from the top of the page

### Switching Later

You can re-run `./scripts/install.sh` at any time to switch HTTPS modes. The script will update the Caddyfile and `.env` file, then rebuild the containers.

### Manual Configuration

If you prefer to configure manually instead of using the install script:
1. Copy `.env.example` to `.env` and fill in your values
2. The install script generates the appropriate `Caddyfile`, or you can edit it directly
3. Run `docker compose up -d --build` to apply changes

## Port Forwarding & LAN Access

Port forwarding is needed only if you want direct access from outside your network. DNS-challenge certificate issuance does not require opening inbound router ports. Tailscale is an alternative for private remote access.

### Port Forwarding

Forward these ports on your router to the Pi's local IP:

| Protocol | External Port | Internal Port | Destination |
|----------|--------------|---------------|-------------|
| TCP | 80 | 80 | Pi's local IP (e.g., `192.168.1.68`) |
| TCP | 443 | 443 | Pi's local IP |

The exact steps vary by router. Generally: log into your router's admin page, find **Port Forwarding** (sometimes under Firewall or NAT), and create rules for ports 80 and 443 pointing to the Pi.

### DHCP Reservation

Port forwarding rules target a specific local IP. If the Pi's IP changes (DHCP lease renewal), the rules break. Set up a **DHCP reservation** (also called a static lease) in your router to permanently assign the Pi's current IP to its MAC address. This is usually found near the DHCP settings in your router's admin page.

### Accessing from Inside Your LAN

The Pi itself accesses the frame via `localhost` (the kiosk uses this automatically). For other devices on your local network, some routers support **hairpin NAT** and the domain will work from inside the LAN too. If your router doesn't, you have a few options:

1. **By hostname** — If your system runs Avahi/mDNS (default on Raspberry Pi OS), use `https://<hostname>.local` (e.g., `https://raspberrypi.local`) from other devices on the LAN.

2. **Via Tailscale** — If Tailscale is installed, use the Tailscale IP (e.g., `https://100.x.x.x`) from any device on your Tailnet, regardless of network.

3. **By local IP** — Access `https://192.168.1.68` (your Pi's IP). This requires adding the IP to the Caddyfile's localhost block:

   ```
   localhost, 192.168.1.68 {
       tls internal
       reverse_proxy photo-frame:5000
   }
   ```

   Then restart Caddy: `docker compose restart caddy`. This uses a self-signed certificate (accept the browser warning once).

## Remote Access (Tailscale)

[Tailscale](https://tailscale.com) creates a secure mesh VPN so you can access your photo frame from anywhere without opening ports on your router.

### Setup

During install, choose "Install Tailscale for secure remote access". The script will:
1. Install Tailscale
2. Run `tailscale up` to authenticate with your Tailscale account
3. Display your Tailscale IP address

### Finding Your Tailscale IP

- During install, the IP is printed to the terminal
- In the web UI, the admin settings panel shows both local and Tailscale IPs (after changing the default password)
- On the Pi: `tailscale ip -4`

### Using with CI/CD

Set your `PI_HOST` secret to the Tailscale IP (e.g., `100.x.x.x`). The GitHub Actions runner needs Tailscale access to reach the Pi — either install Tailscale on the runner or use a Tailscale subnet router.

[Back to overview](../README.md)
