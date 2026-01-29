#! /usr/bin/env bash
set -o xtrace

# usage:
# ./sync.sh 2025-amsterdam-q4 --dry-run
# find . -name "2025-*" -exec ./sync.sh {} \;

rsync -var \
    --exclude "_db/*" \
    --exclude "assets/*" \
    --exclude "_templates/venue.html" \
    --exclude "metadata.yml" \
    _event_template/ $1 $2
