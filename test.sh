#!/bin/bash

set -euo pipefail

PYTHON_BIN="python3"
LOCAL_VENV_PYTHON="${HOME}/.virtualenvs/buildstrap/bin/python3"
if [ -x "${LOCAL_VENV_PYTHON}" ]; then
  PYTHON_BIN="${LOCAL_VENV_PYTHON}"
fi

exec "${PYTHON_BIN}" -m unittest discover -s tests -v
