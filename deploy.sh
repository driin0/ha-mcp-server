#!/bin/sh
# Deploy di ha-mcp-server: git pull + pull dell'immagine + restart.
# Compatibile con sh POSIX. Eseguire dalla root del progetto dopo il clone.
#
# compose.yaml usa un'immagine pubblicata, non `build:`. Chi lavora sul codice
# sostituisce `image:` con `build: .` e usa `docker compose up -d --build`.

set -e

cd "$(dirname "$0")"

[ -f .env ] || { echo "manca .env — copiarlo da .env.sample e compilarlo"; exit 1; }

echo "▶ git pull"
git pull --ff-only

echo "▶ docker compose pull"
docker compose pull

echo "▶ docker compose up -d"
docker compose up -d

echo "✓ Deploy completato"
docker compose ps
