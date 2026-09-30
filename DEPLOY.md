# Deploying lake-mood

## Local development

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

NWS_UA="(lake-mood dev, you@example.com)" \
DB_PATH=./lake.db \
.venv/bin/uvicorn app.main:app --port 9011
```

Then open <http://127.0.0.1:9011/>. On an empty database the poller backfills
seven days of KGPM observations before the first live poll, which takes ~10-20
seconds; until it finishes the page renders with empty tables and `/health`
returns 503.

## Tests

```sh
.venv/bin/pytest -q
```

The tests are offline — parsers run against inline fixtures, and the verdict
module is pure.

## Container

```sh
docker build -t lake-mood .
docker compose up -d --build
```

## Production: aurora (lake.zoleb.com)

The public dashboard is <https://lake.zoleb.com>, served from aurora (the Hetzner
VPS). Cloudflare proxies it; nginx on aurora terminates TLS with the zoleb.com
Origin CA cert and proxies to the container on loopback.

| Piece | Where |
|---|---|
| Deploy dir | `~/lake-mood-deploy` on aurora |
| Source checkout | `~/lake-mood-deploy/.git-clone` (clone of this repo) |
| Compose file | `~/lake-mood-deploy/compose.yaml` (aurora-specific, not this repo's) |
| Env | `~/lake-mood-deploy/.env` (mode 0600; real `NWS_UA`) |
| Data | `~/lake-mood-deploy/data/lake.db` (bind-mounted at `/data`) |
| Port | `127.0.0.1:9011` only — never publish on `0.0.0.0` |
| nginx vhost | `/etc/nginx/sites-available/lake.zoleb.com.conf` (staged copy in the deploy dir) |
| DNS | Cloudflare `lake.zoleb.com`, proxied A + AAAA → aurora's web origin |
| Access | Cloudflare Access app **lake.zoleb.com (public bypass)** — without it the `*.zoleb.com` gate would send visitors to an OTP login |

The aurora compose file differs from this repo's `compose.yaml` in two ways:
it binds `127.0.0.1:9011:9011`, and it mounts `./data` instead of the Synology
path.

### Upgrading

```sh
ssh aurora
cd ~/lake-mood-deploy
git -C .git-clone pull --ff-only
docker compose up -d --build
```

Data is untouched — it lives outside the image.

### Checks

```sh
docker logs --tail 50 lake-mood
curl -s localhost:9011/health                      # on aurora
curl -s https://lake.zoleb.com/health              # through Cloudflare
```

`/health` returns 503 if the database has no observations yet or the observation
poller has failed five times in a row; the container HEALTHCHECK uses the same
endpoint. The public page must return **200**, not a 302 — a 302 means the
Access bypass app is missing and the `*.zoleb.com` gate is catching it.

### History

Until 2026-09-30 the service ran on kappa (Synology NAS) at `http://lakemood` /
`lakemood.lan`, via a router dnsmasq record and a hand-written DSM nginx vhost.
It moved to aurora with its full observation history (SQLite online-backup
snapshot copied across); the kappa container, DNS record and vhost were retired.
