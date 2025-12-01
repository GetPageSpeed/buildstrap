#!/usr/bin/env bash

set -euo pipefail

# Always run from the buildstrap directory so matrix.sh is in a known location
cd "$(dirname "${BASH_SOURCE[0]}")"

# Load distro matrix (defines the 'dists' associative array)
source ./matrix.sh

if [ "$#" -gt 0 ]; then
  # If specific dists are passed, only pull those
  dists_to_pull=("$@")
else
  # Otherwise pull all known dists from the matrix
  dists_to_pull=("${!dists[@]}")
fi

for dist in "${dists_to_pull[@]}"; do
  echo "Pulling getpagespeed/rpmbuilder:${dist} ..."
  docker pull "getpagespeed/rpmbuilder:${dist}"
done












