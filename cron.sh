#!/bin/bash

cd "$(dirname "$0")"

# get latest specs (matrix.yml)
git pull >/dev/null
# cleanup gitignored stuff (leftovers of previous jobs maybe)
git clean -fX >/dev/null

git checkout main

# Sync rpmbuilder BEFORE generate_config.py writes distro_versions.json and
# defaults into it. Pulling afterwards aborts on the dirty overlap, the commit
# lands on a stale base, and a force push then erased upstream commits
# (rpmbuilder 118b076, 2026-10-30). The checkout is a pure mirror: anything
# local is regenerated below.
git -C ../rpmbuilder fetch --quiet origin
git -C ../rpmbuilder reset --hard --quiet '@{u}'

./generate_config.py
git add --all .
git commit -m "Updated matrix.json from lastversion poll [skip ci]"

# push all branches
git push --force --all origin
git checkout main

# navigate to ../rpmbuilder and commit any changes
cd ../rpmbuilder || exit 1
git add --all .
git commit -m "Updated from buildstrap"
# Never force: a rejected push means upstream moved; the next run rebases.
git push origin HEAD
