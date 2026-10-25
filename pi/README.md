# Pi companion server

A small server for a Raspberry Pi that keeps a permanent copy of everything the
app records. The phone keeps only a few days of raw 1 Hz data live; this keeps
all of it, plus the derived day results, in one SQLite file you can query.

It needs no app code changes. The app already has an opt-in "contribute my
health data" upload (`lib/telemetry/health_uploader.dart`) that sends a gzipped
snapshot of its database once a day, on Wi-Fi and while charging, to a
companion URL. This server is that companion. Pointing a build at it is two
`--dart-define` flags.

## What it stores

Everything lives under the data directory (`/srv/whalternative` below):

| Path | What |
|---|---|
| `store.db` | Permanent merged store: `decoded_onehz`, `decoded_rr`, `day_result`, `metric_series`, plus an `uploads` log. Never pruned. |
| `latest.db` | The newest snapshot, as uploaded, for any table not merged. |
| `snapshots/<install id>/*.db.gz` | The last 14 uploads, as received. |

Each table is merged with `INSERT OR REPLACE` on the app's own primary key, so
re-uploads are harmless and the newest value wins, the same rule the app uses.
Daily snapshots overlap by several days, so a missed upload loses nothing as
long as one lands before the phone prunes (about 5 days, at most 14).

## Set up the Pi

Use a USB SSD for `/srv` if you can. SD cards wear out under daily database
writes.

```sh
# 1. Code and data directories
sudo mkdir -p /opt/whalternative /srv/whalternative
sudo cp pi/server.py /opt/whalternative/
sudo chown pi:pi /srv/whalternative

# 2. A secret token (it becomes part of the URL)
echo "WHALT_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" \
  | sudo tee /etc/whalternative.env
sudo chmod 600 /etc/whalternative.env

# 3. Run it as a service
sudo cp pi/whalternative.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now whalternative
journalctl -u whalternative -f      # should say "listening on 127.0.0.1:8787"

# 4. HTTPS through Tailscale (Android blocks plain HTTP)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale serve --bg 8787
tailscale serve status              # shows https://<pi>.<tailnet>.ts.net
```

Install Tailscale on the phone too and sign in to the same account. The phone
then reaches the Pi from anywhere, not only at home.

Check it from any device on the tailnet:

```sh
curl https://<pi>.<tailnet>.ts.net/<token>/summary
```

## Build the app against it

```sh
flutter build apk --release \
  --dart-define=ENABLE_HEALTH_DATA_CONTRIBUTION=true \
  --dart-define=COMPANION_URL=https://<pi>.<tailnet>.ts.net/<token>
```

Then in the app, turn on **Settings › Privacy › Contribute my health data**.
The wording is upstream's; in this build the data goes only to your Pi.

The first upload happens on the next "heavy" sync (after a night's data is
finalized) while the phone is on Wi-Fi and charging. Overnight on the charger
is the normal case. `/summary` shows the last upload and row counts, and
`/coverage` shows stored seconds per day, so gaps are easy to spot.

This build has the same Android package id as the released edge app but a
different signing key, so Android will not install one over the other. Export
a backup from the released app first (Settings › Data), uninstall it, install
this build, and restore the backup.

## Routes

All routes sit under `/<token>`; anything else gets a 404.

| Route | |
|---|---|
| `POST /health/upload` | The app's daily snapshot. Verified against its `sha256`, kept, merged. |
| `POST /consent` | Logged to `consent_log`. |
| `POST /telemetry` | Accepted and dropped. |
| `GET /app/status` | Always "nothing to announce", so the app shows no update banners. |
| `GET /summary` | Row counts, raw data span, last upload. |
| `GET /coverage` | Raw seconds stored per UTC day. |

## Tests

Standard library only, no install needed:

```sh
python3 -m unittest discover -s pi/tests
```

`tests/app_schema_v58.sql` is the real DDL of the merged tables, taken from
`LocalDb.exportCopy()` at schema version 58. Regenerate it when the app's
`schemaVersion` changes.
