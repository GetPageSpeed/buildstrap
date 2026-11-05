"""Regression test: a retired distro builds nothing but keeps its names.

SLES was retired 2026-11-06 (reminder 152CAF9D). matrix.json keeps the
`sles` key (getpagespeed.com sync-distro-matrix.php hard-fails without it)
and matrix.sh keeps os_long[sles] (refresh-repo.sh repoview titles), while
dists, distro_versions.json and defaults drop every SLES entry.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

LASTVERSION_STUB = """\
class _Version:
    def __init__(self, major):
        self.release = (major,)


LATEST = {"rhel": 10, "sles": 16}


def latest(name):
    return _Version(LATEST[name])
"""

MATRIX_YML = """\
distro_defaults:
  os_versions: 2
distros:
  rhel:
    dist: el
    dir: redhat
    description: "CentOS/RHEL"
    rpmbuilder_name: centos
  sles:
    dist: sles
    description: "SUSE Linux Enterprise"
    rpmbuilder_name: opensuse
    os_versions: 1
    retired: true
    retired_versions: [15, 16]
"""


class GenerateConfigRetiredTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        buildstrap = self.tmp / "buildstrap"
        buildstrap.mkdir()
        (self.tmp / "rpmbuilder").mkdir()
        stub = self.tmp / "stub"
        stub.mkdir()
        shutil.copy(REPO_ROOT / "generate_config.py", buildstrap)
        (buildstrap / "matrix.yml").write_text(MATRIX_YML)
        (stub / "lastversion.py").write_text(LASTVERSION_STUB)
        subprocess.run(
            [sys.executable, str(buildstrap / "generate_config.py")],
            env={"PYTHONPATH": str(stub), "PATH": "/usr/bin:/bin"},
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.buildstrap = buildstrap

    def test_matrix_json_keeps_retired_key_without_versions(self):
        matrix = json.loads((self.buildstrap / "matrix.json").read_text())
        sles = matrix["distros"]["sles"]
        self.assertTrue(sles["retired"])
        self.assertEqual(sles["versions"], [])
        self.assertEqual(sles["retired_versions"], [15, 16])
        self.assertEqual(matrix["distros"]["rhel"]["versions"], [10, 9])

    def test_matrix_sh_drops_dists_but_keeps_os_long(self):
        matrix_sh = (self.buildstrap / "matrix.sh").read_text()
        dists = matrix_sh.split("declare -A os_long")[0]
        self.assertNotIn("sles", dists)
        self.assertIn('["el10"]="redhat/10"', dists)
        self.assertIn('["sles"]="SUSE Linux Enterprise"', matrix_sh)

    def test_rpmbuilder_artifacts_drop_retired_images(self):
        rpmbuilder = self.tmp / "rpmbuilder"
        versions = json.loads((rpmbuilder / "distro_versions.json").read_text())
        self.assertEqual(
            versions["include"],
            [{"os": "centos", "version": 10}, {"os": "centos", "version": 9}],
        )
        self.assertNotIn("opensuse", (rpmbuilder / "defaults").read_text())


if __name__ == "__main__":
    unittest.main()
