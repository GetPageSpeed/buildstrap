#!/bin/bash

cd "$(dirname "$0")"

# get latest specs (matrix.yml)
git pull >/dev/null
# cleanup gitignored stuff (leftovers of previous jobs maybe)
git clean -fX >/dev/null

git checkout main
./generate-config.py
git add --all .
git commit -m "Updated generated_config.yml"

# push all branches
git push --force --all origin
git checkout master