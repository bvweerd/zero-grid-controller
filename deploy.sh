#!/bin/bash
set -e

SRC="$(dirname "$0")/custom_components/zero_grid_controller"
DEST="/media/data/homeassistant/config/custom_components/zero_grid_controller"

echo "Deploying zero_grid_controller to HA..."
rsync -av --delete "$SRC/" "$DEST/"
echo "Done."
