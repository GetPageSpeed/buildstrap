"""Regression tests for the RPM CircleCI configuration generator."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional

from ruamel.yaml import YAML


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "generate_circleci_config.py"

NOARCH_SPEC = """\
Name: {name}
Version: 1
Release: 1
Summary: Test package
License: MIT
BuildArch: noarch
"""

ARCH_SPEC = """\
Name: {name}
Version: 1
Release: 1
Summary: Test package
License: MIT
"""


class GenerateCircleCIConfigTest(unittest.TestCase):
    """Exercise architecture selection through the generator CLI."""

    def generate(
        self, specs: Dict[str, str], settings: Optional[str] = None
    ) -> Dict[str, object]:
        """Generate and parse a config for a temporary packaging project."""
        with tempfile.TemporaryDirectory() as project_dir_string:
            project_dir = Path(project_dir_string)
            for filename, contents in specs.items():
                (project_dir / filename).write_text(contents, encoding="utf-8")
            if settings is not None:
                (project_dir / "settings.yml").write_text(settings, encoding="utf-8")

            subprocess.run(
                [sys.executable, str(GENERATOR), "--project-dir", str(project_dir)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            config_file = project_dir / ".circleci" / "config.yml"
            with config_file.open(encoding="utf-8") as stream:
                return YAML(typ="safe").load(stream)

    def assert_only_x86_64_workflows(self, config: Dict[str, object]) -> None:
        """Assert every emitted workflow and deploy job targets x86_64."""
        workflows = config["workflows"]
        self.assertTrue(workflows)
        self.assertTrue(all(name.endswith("-x86_64") for name in workflows))

        deploy_arches = {
            job["deploy"]["arch"]
            for workflow in workflows.values()
            for job in workflow["jobs"]
            if "deploy" in job
        }
        self.assertEqual(deploy_arches, {"x86_64"})

    def test_single_noarch_spec_builds_only_on_x86_64(self) -> None:
        config = self.generate({"one.spec": NOARCH_SPEC.format(name="one")})

        self.assert_only_x86_64_workflows(config)
        self.assertEqual(
            config["jobs"]["build"]["parameters"]["resource_class"]["default"],
            "small",
        )

    def test_multiple_noarch_specs_build_only_on_x86_64(self) -> None:
        config = self.generate(
            {
                "one.spec": NOARCH_SPEC.format(name="one"),
                "two.spec": NOARCH_SPEC.format(name="two"),
            }
        )

        self.assert_only_x86_64_workflows(config)

    def test_mixed_specs_keep_both_architectures(self) -> None:
        config = self.generate(
            {
                "one.spec": NOARCH_SPEC.format(name="one"),
                "two.spec": ARCH_SPEC.format(name="two"),
            }
        )

        workflow_names = config["workflows"]
        self.assertTrue(any(name.endswith("-x86_64") for name in workflow_names))
        self.assertTrue(any(name.endswith("-aarch64") for name in workflow_names))

    def test_subpackage_noarch_does_not_collapse_project_matrix(self) -> None:
        spec = (
            ARCH_SPEC.format(name="mixed")
            + """\

%package docs
Summary: Architecture-independent documentation
BuildArch: noarch
"""
        )
        config = self.generate({"mixed.spec": spec})

        workflow_names = config["workflows"]
        self.assertTrue(any(name.endswith("-x86_64") for name in workflow_names))
        self.assertTrue(any(name.endswith("-aarch64") for name in workflow_names))

    def test_legacy_explicit_noarch_is_normalized_to_x86_64(self) -> None:
        config = self.generate(
            {"one.spec": NOARCH_SPEC.format(name="one")},
            settings="archs:\n  - noarch\n",
        )

        self.assert_only_x86_64_workflows(config)

    def test_explicit_real_architectures_override_noarch_detection(self) -> None:
        config = self.generate(
            {"one.spec": NOARCH_SPEC.format(name="one")},
            settings="archs:\n  - x86_64\n  - aarch64\n",
        )

        workflow_names = config["workflows"]
        self.assertTrue(any(name.endswith("-x86_64") for name in workflow_names))
        self.assertTrue(any(name.endswith("-aarch64") for name in workflow_names))

    def test_explicit_empty_architecture_list_stays_empty(self) -> None:
        config = self.generate(
            {"one.spec": NOARCH_SPEC.format(name="one")}, settings="archs: []\n"
        )

        self.assertEqual(config["workflows"], {})

    def test_git_branch_override_preserves_collection_channel(self) -> None:
        config = self.generate(
            {"vmod.spec": ARCH_SPEC.format(name="vmod")},
            settings=(
                "collection: varnish\n"
                "git_branch: master\n"
                "resource_class: small\n"
            ),
        )

        for workflow in config["workflows"].values():
            build = next(job["build"] for job in workflow["jobs"] if "build" in job)
            self.assertEqual(
                build["filters"]["branches"]["only"],
                ["main", "master", "stable"],
            )
            self.assertEqual(
                build["enable_repos"], "getpagespeed-extras-varnish60"
            )


if __name__ == "__main__":
    unittest.main()
