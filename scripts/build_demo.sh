#!/usr/bin/env bash
# Rebuilds the static demo in docs/ (served by GitHub Pages) from a fresh run:
#   1. generate the month as of Nov 1 and run the close    -> docs/data/before.json (BLOCKED)
#   2. apply the Nov 2 ERP fixes and run the close again    -> docs/data/after.json  (READY)
# Needs a MySQL server: start one with  docker compose up -d mysql
set -euo pipefail
cd "$(dirname "$0")/.."

python -m generator.generate
python -c "from pipeline.db import connect; c = connect(); c.cursor().execute('DROP DATABASE IF EXISTS mfg_dw'); c.commit()"
python -m pipeline.run --close-period 2026-10 --full --export docs/data --label before
python -m generator.generate --corrections
python -m pipeline.run --close-period 2026-10 --export docs/data --label after
cp app/static/index.html docs/index.html
touch docs/.nojekyll
echo "Demo rebuilt in docs/. Commit and push; GitHub Pages serves it."
