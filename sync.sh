#! /usr/bin/env bash
set -o xtrace

# usage:
# ./sync.sh 2026-nyc-q4 --dry-run
# find . -maxdepth 1 -name "2026-*" -exec ./sync.sh {} \;
# (upcoming events at the top; finished ones in past/ keep the version they ended with)

rsync -var \
    --exclude "_db/*" \
    --exclude "assets/*" \
    --exclude "_templates/venue.html" \
    --exclude "metadata.yml" \
    _event_template/ $1 $2
