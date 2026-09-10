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

## Synology NAS

`scp` is disabled on the NAS, so ship the repo through a pipe to `ssh`:

```sh
git archive --format=tar.gz HEAD | ssh magehands@192.168.1.248 'cat > /tmp/lake-mood.tgz'
```

Then on the NAS:

```sh
mkdir -p /volume1/docker/lake-mood /volume1/docker/lake-mood-data
tar xzf /tmp/lake-mood.tgz -C /volume1/docker/lake-mood
```

`.env` is gitignored, so it is not in the archive — copy it over separately
(e.g. `ssh magehands@192.168.1.248 'cat > /volume1/docker/lake-mood/.env' < .env`)
or write it in place from `.env.example`. It must set a real `NWS_UA`; the NWS
API returns 403 for the placeholder.

Finally:

```sh
cd /volume1/docker/lake-mood && docker-compose up -d --build
```

The DSM package ships Compose v2 as `docker-compose`. The dashboard is then on
port **9011**; the SQLite file lives at
`/volume1/docker/lake-mood-data/lake.db` and survives rebuilds.

### Upgrading

Repeat the `git archive` + `tar` steps and re-run `docker-compose up -d --build`.
Data is untouched — the volume is outside the image.

### Checks

```sh
docker logs --tail 50 lake-mood
curl -s localhost:9011/health
```

`/health` returns 503 if the database has no observations yet or the observation
poller has failed five times in a row; the container HEALTHCHECK uses the same
endpoint.

## Short name (http://lakemood)

The dashboard is reachable at `http://lakemood` on the home LAN via two pieces of configuration:

### DNS

The ASUS router (Asuswrt-Merlin, 192.168.1.1) resolves `lakemood` and `lakemood.lan`
to kappa (192.168.1.248). This is configured in `/jffs/configs/dnsmasq.conf.add`:

```
address=/lakemood/lakemood.lan/192.168.1.248
```

Applied with `service restart_dnsmasq` on the router. A backup of the previous file
is at `/jffs/configs/dnsmasq.conf.add.bak-20260910`. Note that `.local` was avoided
on purpose — Apple devices resolve `*.local` only via mDNS, never via the router.

### Port 80

DSM's nginx on kappa proxies these hostnames to the container via a manual vhost at
`/usr/local/etc/nginx/sites-enabled/lakemood.conf`:

```
server {
    listen 80;
    listen [::]:80;
    server_name lakemood lakemood.lan;

    location / {
        proxy_pass http://127.0.0.1:9011;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Reload with `nginx -t && synosystemctl reload nginx` as root.

**Warning:** A DSM update can regenerate the nginx config and drop this file. If
`http://lakemood` stops working but `http://lakemood:9011` still does, recreate
the vhost file.
