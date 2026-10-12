# Run with Docker (home server + phone access)

## 1. Run it

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker Engine + Compose (Linux). Start it.
2. In the project folder, copy `.env.example` to `.env` and set `APP_PASSWORD` to a long random password.
3. Build and start:

   ```
   docker compose up -d --build
   ```

4. Open http://localhost:8000. Username can be anything; the password is `APP_PASSWORD`.
5. Optional demo data:

   ```
   docker compose exec web python seed.py
   ```

Two containers run: `web` (the site) and `scraper` (runs the scraper once a day).
Both share one Docker volume (`grocery-data`) that holds the SQLite database, so your
data survives restarts and rebuilds. Both restart automatically unless you stop them.

Handy commands:

```
docker compose ps                 # status
docker compose logs -f web        # web logs
docker compose logs scraper       # scrape results
docker compose exec scraper python -m app.scraper   # scrape right now
docker compose down               # stop (data kept)
git pull && docker compose up -d --build            # update
```

Back up the database:

```
docker compose cp web:/data/grocery.db ./grocery-backup.db
```

## 2. Reach it from your phone away from home (Tailscale)

Tailscale creates a private encrypted network between your devices. No router
changes, and nothing is exposed to the internet.

1. Create a free account at https://tailscale.com and install Tailscale on the
   **computer running Docker** and on your **phone**. Sign in to the same account on both.
2. On the Docker computer, find its Tailscale address (the Tailscale app shows the
   device name and a `100.x.y.z` IP; or run `tailscale ip -4`).
3. On your phone (Tailscale turned on), open `http://100.x.y.z:8000` and sign in with
   your password. Bookmark it or add it to your home screen.

The Docker computer must be on and awake. On Windows, turn off sleep while plugged in.

### Do not do this
Don't port-forward 8000 on your router. The login is HTTP Basic auth, which is
unencrypted over plain HTTP. Tailscale already encrypts the traffic.

### Want a public HTTPS address instead?
Cloudflare Tunnel can give you `https://something.yourdomain.com` without opening
ports, but it needs a domain you own and makes the app reachable by anyone who finds it,
so use a strong password. Tailscale is the safer choice for a personal app.
