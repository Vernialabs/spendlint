# spendlint telemetry collector

The code running at `https://t.vernia.in`. A Cloudflare Worker receives spendlint's usage events
(schema: [docs/telemetry.md](../docs/telemetry.md)), rejects anything that does not match the schema exactly, and
stores one row per run in a Cloudflare D1 database ([schema.sql](schema.sql)). It never reads or stores IP
addresses or request headers, and Worker request logs are turned off in [wrangler.toml](wrangler.toml).

## Deploy

Needs Node.js 18 or newer and a Cloudflare account with `vernia.in` added as a zone.

```bash
cd collector
npm ci
npx wrangler login                                  # opens the browser once
npx wrangler d1 create spendlint-telemetry          # copy the printed database_id into wrangler.toml
npm run db:init:remote                              # creates the events table
npm run deploy                                      # publishes the Worker on t.vernia.in
```

Check it:

```bash
curl https://t.vernia.in/
```

## Develop

```bash
npm test                 # schema validation tests
npm run db:init:local    # local D1 database
npm run dev              # http://localhost:8787/v1/events
```

## Read the data

```bash
npx wrangler d1 execute spendlint-telemetry --remote --command "SELECT received_day, COUNT(*) AS runs, COUNT(DISTINCT install_id) AS installs FROM events GROUP BY received_day ORDER BY received_day DESC LIMIT 14"
```

Findings per rule:

```bash
npx wrangler d1 execute spendlint-telemetry --remote --command "SELECT j.key AS rule, SUM(j.value) AS findings FROM events, json_each(events.findings_by_rule) AS j GROUP BY j.key ORDER BY findings DESC"
```
