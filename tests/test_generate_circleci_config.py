"""Regression tests for the RPM CircleCI configuration generator."""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional

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
            generated_text = config_file.read_text(encoding="utf-8")
            self.assertFalse(
                any(line != line.rstrip() for line in generated_text.splitlines()),
                "generated config contains trailing whitespace",
            )
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

    def test_plesk_channel_from_master_skips_distros_without_plesk(self) -> None:
        """has_plesk=False (el10) must apply even when git_branch overrides plesk.

        sw-nginx-compat builds the plesk channel from master; before 2026-09-02
        the exclusion keyed on git_branch and emitted an el10-plesk lane.
        """
        config = self.generate(
            {"sw-nginx-compat.spec": ARCH_SPEC.format(name="sw-nginx-compat")},
            settings=(
                "collection: nginx\n"
                "branches:\n"
                "  plesk:\n"
                "    description: Plesk\n"
                "    plesk_version: 18\n"
                "    git_branch: master\n"
                "    only_dists:\n"
                "      - \"el*\"\n"
                "    only_archs:\n"
                "      - x86_64\n"
            ),
        )

        workflows = set(config["workflows"])
        self.assertEqual(
            workflows,
            {"build-deploy-el7-x86_64", "build-deploy-el8-x86_64", "build-deploy-el9-x86_64"},
        )
        for workflow in config["workflows"].values():
            build = next(job["build"] for job in workflow["jobs"] if "build" in job)
            self.assertEqual(build["plesk"], 18)
            self.assertEqual(build["enable_repos"], "getpagespeed-extras-plesk")
            self.assertEqual(build["filters"]["branches"]["only"], ["main", "master", "stable"])

    def test_standalone_repo_can_enable_its_publish_channel(self) -> None:
        config = self.generate(
            {"php.spec": ARCH_SPEC.format(name="php")},
            settings=(
                "git_branch: php84\n"
                "enable_repos: getpagespeed-extras-php84\n"
                "dists:\n"
                "  - el7\n"
                "archs:\n"
                "  - x86_64\n"
            ),
        )

        self.assertTrue(config["workflows"])
        for workflow in config["workflows"].values():
            build = next(job["build"] for job in workflow["jobs"] if "build" in job)
            self.assertEqual(build["filters"]["branches"]["only"], ["php84"])
            self.assertEqual(build["enable_repos"], "getpagespeed-extras-php84")

    @staticmethod
    def deploy_commands(config: Dict[str, object]) -> List[str]:
        """Return every shell command the deploy job runs."""
        return [
            step["run"]["command"]
            for step in config["jobs"]["deploy"]["steps"]
            if isinstance(step, dict) and "run" in step
        ]

    @staticmethod
    def expand_untagged(fragment: str) -> str:
        """Expand a command fragment the way a tag build's shell would.

        CircleCI leaves ``CIRCLE_BRANCH`` unset on tag-triggered pipelines,
        which is exactly when the deploy jobs run in self mode.
        """
        env = dict(os.environ)
        env.pop("CIRCLE_BRANCH", None)
        env.update(
            {"CIRCLE_PROJECT_REPONAME": "ngm", "DISTRO": "el9", "ARCH": "x86_64"}
        )
        # The fragment is inlined into the script (not passed as $1) so the
        # shell performs the same parameter expansion CircleCI would.
        return subprocess.run(
            ["sh", "-c", 'printf "%s" "' + fragment + '"'],
            check=True,
            env=env,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        ).stdout

    def test_deploy_paths_never_lose_the_branch_component_on_tag_builds(self) -> None:
        """A tag build must still land under a recognized branch directory.

        Deploy jobs are tag-only, and CircleCI leaves ``CIRCLE_BRANCH`` empty on
        tag pipelines, so a bare ``${CIRCLE_BRANCH}`` produced
        ``~/incoming/ngm/el9/x86_64/``. incoming.sh then read the basename as
        ``x86_64``, refused to integrate, and every upload was silently dropped
        (ngm v0.0.23 and v0.0.24, 2026-08-28).
        """
        config = self.generate(
            {"one.spec": NOARCH_SPEC.format(name="one")}, settings="self: true\n"
        )

        commands = self.deploy_commands(config)
        self.assertTrue(commands)

        paths = []
        for command in commands:
            self.assertNotIn(
                "${CIRCLE_BRANCH}",
                command,
                "deploy command uses an undefaulted CIRCLE_BRANCH",
            )
            # Every token carrying the branch expansion is a path the build
            # server has to be able to parse back into <dist>/<arch>/<branch>.
            paths.extend(
                token.strip('"')
                for token in re.findall(r"\S*\$\{CIRCLE_BRANCH[^}]*\}\S*", command)
            )
        self.assertEqual(len(paths), 4, paths)

        for path in paths:
            expanded = self.expand_untagged(path)
            self.assertNotIn("//", expanded, path)
            self.assertIn("/x86_64/master", expanded, path)

    def test_non_self_deploy_commands_are_unchanged(self) -> None:
        """Consumers that cannot hit the tag-build bug keep byte-identical CI.

        Non-self deploy jobs are branch-filtered, so CIRCLE_BRANCH is always
        populated and the ``:-master`` default would never fire. Emitting it
        anyway would still rewrite the ``command`` strings, which is a semantic
        diff under ensure-latest.sh's [skip ci] rule — every packaging repo in
        the fleet would push a CI-firing commit (~18 workflows each) for no
        behavioral change. Pin the exact strings so a future edit to the deploy
        steps has to face that cost deliberately.
        """
        config = self.generate({"one.spec": NOARCH_SPEC.format(name="one")})

        incoming = "~/incoming/${CIRCLE_PROJECT_REPONAME}/${DISTRO}/${ARCH}/${CIRCLE_BRANCH}"
        self.assertEqual(
            self.deploy_commands(config),
            [
                "ssh -o StrictHostKeyChecking=no $GPS_BUILD_USER@$GPS_BUILD_SERVER"
                f' "mkdir -p {incoming}"',
                "scp -o StrictHostKeyChecking=no -q -r *.rpm"
                " $GPS_BUILD_USER@$GPS_BUILD_SERVER:"
                "~/incoming/${CIRCLE_PROJECT_REPONAME}/${DISTRO}/${ARCH}/${CIRCLE_BRANCH}/",
                "ssh -o StrictHostKeyChecking=no -q $GPS_BUILD_USER@$GPS_BUILD_SERVER"
                ' "nohup ~/scripts/incoming.sh'
                " ${CIRCLE_PROJECT_REPONAME}/${DISTRO}/${ARCH}/${CIRCLE_BRANCH}/"
                " > ~/incoming/$CIRCLE_PROJECT_REPONAME/$DISTRO/${ARCH}/${CIRCLE_BRANCH}"
                '/process.log 2>&1&"',
            ],
        )

    def test_build_and_deploy_jobs_carry_least_privilege_contexts(self) -> None:
        """Build jobs must not carry the package-repo publish credentials.

        `org-global` bundled the repo-read build tokens with GPS_BUILD_* (the
        ability to put an RPM in front of every customer) and was handed to
        every job on whichever runner myci picked — four of six are shared
        customer production boxes. Since 2026-08-30 myci refuses to inject the
        `deploy` context on a runner not marked `trusted: true`, so regenerating
        a config must keep the two halves apart.
        """
        config = self.generate(
            {"one.spec": ARCH_SPEC.format(name="one")},
            settings=(
                "post_deploy_smoke:\n"
                "  master:\n"
                "    dists: [el9]\n"
                "    archs: [x86_64]\n"
            ),
        )

        contexts = {}
        for workflow in config["workflows"].values():
            for job in workflow["jobs"]:
                for job_name, spec in job.items():
                    contexts.setdefault(job_name, set()).add(spec.get("context"))

        self.assertEqual(contexts["build"], {"build-deps"})
        self.assertEqual(contexts["deploy"], {"deploy"})
        if "smoke" in contexts:
            self.assertEqual(contexts["smoke"], {"build-deps"})
        self.assertNotIn("org-global", {c for cs in contexts.values() for c in cs})


if __name__ == "__main__":
    unittest.main()
