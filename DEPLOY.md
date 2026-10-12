# Deploy on a home server (Linux with systemd)

Tested layout for Debian / Ubuntu / Raspberry Pi OS. Run these on the server.

## 1. Install

```bash
sudo apt update && sudo apt install -y git python3 python3-venv
sudo useradd --system --create-home --shell /usr/sbin/nologin grocery
sudo git clone https://github.com/Warden135/fl-grocery-tracker /opt/fl-grocery-tracker
# (private repo: use a personal access token or deploy key, or copy the files with scp)
cd /opt/fl-grocery-tracker
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo chown -R grocery:grocery /opt/fl-grocery-tracker
```

## 2. Configure

```bash
sudo mkdir -p /var/lib/grocery-tracker && sudo chown grocery:grocery /var/lib/grocery-tracker
sudo cp deploy/grocery-tracker.env.example /etc/grocery-tracker.env
sudo nano /etc/grocery-tracker.env     # optional: set APP_PASSWORD, COOKIE_SECURE
sudo chmod 600 /etc/grocery-tracker.env
```

The database lives at `/var/lib/grocery-tracker/grocery.db`; back that file up.
Optional demo data: `sudo -u grocery env GROCERY_DB=/var/lib/grocery-tracker/grocery.db .venv/bin/python seed.py`

## 3. Start on boot + daily scrape

```bash
sudo cp deploy/grocery-tracker.service deploy/grocery-scrape.service deploy/grocery-scrape.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now grocery-tracker.service grocery-scrape.timer
```

Open `http://<server-ip>:8000` from any device on your network. Create an account to get
started. (If you set `APP_PASSWORD`, the browser first asks for it: any username.)

Useful commands:

```bash
systemctl status grocery-tracker          # is it running?
journalctl -u grocery-tracker -f          # web logs
sudo systemctl start grocery-scrape       # run a scrape now
journalctl -u grocery-scrape              # scrape results
systemctl list-timers grocery-scrape.timer
```

## Updating

```bash
cd /opt/fl-grocery-tracker && sudo -u grocery git pull
sudo .venv/bin/pip install -r requirements.txt
sudo systemctl restart grocery-tracker
```

## Reaching it away from home

- **Do not port-forward it to the internet.** HTTP Basic auth over plain HTTP sends the
  password unencrypted.
- Easiest safe option: install [Tailscale](https://tailscale.com) on the server and your
  phone/laptop, then browse to the server's Tailscale address on port 8000.
- Alternative: put a reverse proxy with HTTPS (Caddy or nginx) in front of it first.
