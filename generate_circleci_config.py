#!/usr/bin/env python3
import fnmatch
import os
import argparse
import json
import re

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString, FoldedScalarString


SPEC_SECTION_RE = re.compile(
    r"^%(?:description|package|prep|generate_buildrequires|build|install|check|"
    r"files|pre|post|preun|postun|trigger\w*|changelog)\b",
    re.IGNORECASE,
)


def get_spec_preamble_tag(spec_file, tag):
    """Return a tag value from the main package preamble, if present."""
    tag_re = re.compile(rf"^{re.escape(tag)}\s*:\s*(.*?)\s*(?:#.*)?$", re.IGNORECASE)
    with open(spec_file, "r", encoding="utf-8") as spec_stream:
        for raw_line in spec_stream:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if SPEC_SECTION_RE.match(line):
                break
            match = tag_re.match(line)
            if match:
                return match.group(1).strip()
    return None


def normalize_archs(configured_archs):
    """Map the legacy synthetic noarch lane to its x86_64 build host."""
    normalized_archs = []
    for arch in configured_archs:
        normalized_arch = "x86_64" if arch == "noarch" else arch
        if normalized_arch not in normalized_archs:
            normalized_archs.append(normalized_arch)
    return normalized_archs


# Initialize YAML handler
yaml = YAML()
yaml.default_flow_style = False
# Instruct the representer to ignore aliases
yaml.representer.ignore_aliases = lambda *args: True

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Generate CircleCI configuration.")
parser.add_argument("--project-dir", default=".", help="Root directory of the project.")
args = parser.parse_args()

# Determine the project directory
project_dir = os.path.abspath(args.project_dir)

# Read settings.yml from the project directory
settings_file = os.path.join(project_dir, "settings.yml")
if os.path.exists(settings_file):
    with open(settings_file, "r") as f:
        project_settings = yaml.load(f)
    if project_settings is None:
        project_settings = {}
else:
    project_settings = {}

# Default architectures
default_archs = ["x86_64", "aarch64"]

# Get architectures from settings.yml or default to the default_archs
configured_archs = project_settings.get("archs")
noarch_build = configured_archs == ["noarch"]
archs = normalize_archs(default_archs if configured_archs is None else configured_archs)
exclude_patterns = project_settings.get("exclude", [])
git_branch_override = project_settings.get("git_branch")
if git_branch_override is not None:
    if not isinstance(git_branch_override, str) or not git_branch_override.strip():
        raise ValueError("settings.yml git_branch must be a non-empty string")
    git_branch_override = git_branch_override.strip()
enable_repos_override = project_settings.get("enable_repos")
if enable_repos_override is not None:
    if not isinstance(enable_repos_override, str) or not enable_repos_override.strip():
        raise ValueError("settings.yml enable_repos must be a non-empty string")
    enable_repos_override = enable_repos_override.strip()
# `dists:` is an allowlist (symmetric to `archs:`) over dist / dist-version /
# dist-version-arch fnmatch patterns. Empty list / unset = no allowlist (build
# everywhere except `exclude`). Use this when a repo is intrinsically scoped to
# a subset of the matrix (e.g. libseccomp-rpm is el7-only because newer distros
# already ship libseccomp >= 2.5.x); future new distros are excluded by default
# instead of silently joining the build matrix.
dists_allowlist = project_settings.get("dists", [])

# Self mode: tag-triggered release builds (ngm, fds, stack-scripts).
# Replaces the verbatim generated_config_self.yml template — single boolean
# `self: true` in settings.yml flips the generator to:
#   - no collection / no branch-axis (per-distro × per-arch × single workflow)
#   - build command `./utils/version-from-tag.sh && build`
#   - tag filters (build: /.*/, deploy: /^v.*/ branches ignored)
#   - small resource_class default
#   - no enable_repos / no nginx-collection plumbing
# Any settings.yml knob (e.g. explicit `archs:`) still wins.
self_mode = bool(project_settings.get("self", False))
# A project whose every root spec declares the main package `BuildArch: noarch`
# needs one build host per distro, not one per CPU architecture. Use the real
# x86_64 lane so workflow names, runner selection, and incoming paths never
# pretend that `noarch` is an executor architecture. An explicit real `archs:`
# list still wins. The legacy explicit `archs: [noarch]` spelling is normalized
# above for compatibility.
spec_files = sorted(
    os.path.join(project_dir, filename)
    for filename in os.listdir(project_dir)
    if filename.endswith(".spec")
)
if configured_archs is None and spec_files:
    if all(
        get_spec_preamble_tag(spec_file, "BuildArch") == "noarch"
        for spec_file in spec_files
    ):
        archs = ["x86_64"]
        noarch_build = True
    elif len(spec_files) == 1:
        exclusive_archs = get_spec_preamble_tag(spec_files[0], "ExclusiveArch")
        if exclusive_archs:
            # Specs may list RPM macros (%{arm}, %{?go_arches:...}) or
            # arches we don't build for (i686). Keep only CI-buildable
            # arches; if nothing survives (pure-macro list), keep the
            # default matrix.
            sanitized = [
                arch for arch in exclusive_archs.split() if arch in default_archs
            ]
            if sanitized:
                archs = sanitized
exclude_archs = normalize_archs(project_settings.get("exclude_archs", []))

# Exclude architectures
archs = [arch for arch in archs if arch not in exclude_archs]

# Read matrix.json
matrix_file = os.path.join(os.path.dirname(__file__), "matrix.json")
with open(matrix_file, "r") as f:
    matrix_config = json.load(f)

# Get the branches from matrix.json "collections": { "nginx": { "branches": {
# what branches depends on detected collection, e.g. "nginx"
if self_mode:
    # Tag-triggered: no branch axis. Sentinel single-branch keeps the existing
    # distros × branches × archs loop intact while emitting workflow names
    # without a branch suffix (per get_workflow_name's len(branches) == 1
    # short-circuit). collection_name forced None so nginx-only blocks below
    # (custom setup steps, plesk/mod/failure_tolerance params, enable_repos
    # default) all stay dormant.
    branches = {"__self__": {"description": "tag-triggered self build"}}
    collection_name = None
else:
    branches = {
        "master": {
            "description": "Main release branch",
        }
    }
    collection_name = None
    # if project diredtory nqme starts with "nginx-", set collection_name to "nginx"
    # get base name of the directory
    project_dir_base = os.path.basename(project_dir)
    if project_dir_base.startswith("nginx-"):
        collection_name = "nginx"
    # settings can specify collection name explicitly
    collection_name = project_settings.get("collection", collection_name)
    if collection_name:
        branches = matrix_config["collections"][collection_name]["branches"]
    # project can override branches or specify 'all'
    branches = project_settings.get("branches", branches)
    # project can explicitly specify a set of branches to reduce, using branch:
    # then filter out branches that are not in the list
    if "branch" in project_settings:
        branches = {
            k: v for k, v in branches.items() if k in project_settings["branch"]
        }
    # project can exclude branches, e.g. plesk, by specifying exclude_branches:
    if "exclude_branches" in project_settings:
        branches = {
            k: v
            for k, v in branches.items()
            if k not in project_settings["exclude_branches"]
        }
    if git_branch_override and len(branches) != 1:
        raise ValueError(
            "settings.yml git_branch requires exactly one selected collection branch"
        )

resource_class = "medium"
# Self mode default is small (verbatim template parity).
if self_mode:
    resource_class = "small"
# A noarch build only needs the small x86_64 runner.
if noarch_build:
    resource_class = "small"
# projects may override resource class
resource_class = project_settings.get("resource_class", resource_class)
arm_resource_class_mappings = {"small": "medium"}
arm_resource_class = "arm." + arm_resource_class_mappings.get(
    resource_class, resource_class
)

# Opt-in post-deploy smoke install jobs. Shape (per-project settings.yml):
#   post_deploy_smoke:
#     <branch>:
#       dists: [el9, ...]
#       archs: [x86_64, aarch64]
# Absent / empty → no smoke jobs emitted (default-off; consumers not opting in
# regenerate a byte-identical .circleci/config.yml). Smoke body lives in the
# consumer repo at scripts/smoke.sh; the generated job just `checkout`s and
# runs that script under the rpmbuilder executor.
post_deploy_smoke = project_settings.get("post_deploy_smoke") or {}

command_set_nginx_macros = LiteralScalarString(
    r"""[ -z ${PLESK+x} ] || echo "%plesk ${PLESK}" >> rpmmacros
# we generate both nginx-module-<foo> and sw-nginx-module-<foo> from a single spec file, so:
[ -z ${PLESK+x} ] || (echo >> rpmlint.config && echo 'addFilter ("E: invalid-spec-name")' >> rpmlint.config)
[ -z ${MOD+x} ] || echo "%_nginx_mod ${MOD}" >> rpmmacros
[ -z ${MOD+x} ] || (echo >> rpmlint.config && echo 'addFilter ("E: invalid-spec-name")' >> rpmlint.config)
"""
)

command_spec_files_cleanup = LiteralScalarString(
    r"""[[ ! -f ./cleanup.sh ]] || BRANCH="${CIRCLE_BRANCH}" ./cleanup.sh"""
)

command_check_rpm_files_halt = LiteralScalarString(
    r"""if ls /output/*.rpm 1> /dev/null 2>&1; then
  echo "RPM files found. Proceeding with persistence to workspace."
  ls -al /output/*.rpm
else
  echo "No RPM files found. Halting the job."
  curl --request POST --url https://circleci.com/api/v2/workflow/$CIRCLE_WORKFLOW_ID/cancel --header "Circle-Token: ${CIRCLE_TOKEN}"
  circleci-agent step halt
fi"""
)

# CircleCI leaves CIRCLE_BRANCH empty on tag-triggered pipelines. In self mode
# the deploy job is tag-only (see the filters below), so EVERY self-mode deploy
# interpolated an empty branch component: RPMs landed in
# ~/incoming/<proj>/<dist>/<arch>/ and incoming.sh saw basename "x86_64", which
# is not a recognized deploy target, so it refused to integrate. Uploads
# succeeded, nothing was ever published (ngm v0.0.23 and v0.0.24, 2026-08-28).
# incoming.sh only distinguishes base from non-base branches, and master/main
# are both base, so "master" is a safe fallback whatever the repo's default
# branch is called.
#
# Deliberately scoped to self mode. Non-self deploy jobs are branch-filtered,
# so CIRCLE_BRANCH is always populated for them and the default would never
# fire — but the changed `command` string is a semantic diff under
# ensure-latest.sh's [skip ci] rule, so emitting it fleet-wide would push a
# CI-firing commit to every packaging repo (~18 workflows each) to no effect.
# Consumers that cannot hit the bug keep a byte-identical config.
deploy_branch = "${CIRCLE_BRANCH:-master}" if self_mode else "${CIRCLE_BRANCH}"

command_incoming_mkdir = FoldedScalarString(
    "ssh -o StrictHostKeyChecking=no "
    "$GPS_BUILD_USER@$GPS_BUILD_SERVER "
    f'"mkdir -p ~/incoming/${{CIRCLE_PROJECT_REPONAME}}/${{DISTRO}}/${{ARCH}}/{deploy_branch}"'  # this way quotoing is important otherwise ~ resolves on local machine to /root
)

command_deploy_all_rpms = FoldedScalarString(
    "scp -o StrictHostKeyChecking=no -q -r *.rpm "
    f"$GPS_BUILD_USER@$GPS_BUILD_SERVER:~/incoming/${{CIRCLE_PROJECT_REPONAME}}/${{DISTRO}}/${{ARCH}}/{deploy_branch}/"
)

command_trigger_incoming_hook = FoldedScalarString(
    "ssh -o StrictHostKeyChecking=no -q $GPS_BUILD_USER@$GPS_BUILD_SERVER"
    f' "nohup ~/scripts/incoming.sh ${{CIRCLE_PROJECT_REPONAME}}/${{DISTRO}}/${{ARCH}}/{deploy_branch}/'
    f" > ~/incoming/$CIRCLE_PROJECT_REPONAME/$DISTRO/${{ARCH}}/{deploy_branch}/process.log 2>&1&\""
)

build_steps = [
    "checkout",
]

# TODO migrate to custom_steps_after_checkout: from matrix.yml
if collection_name == "nginx":
    build_steps += [
        {
            "run": {
                "name": "Set up RPM macro reflecting the NGINX branch",
                "command": 'echo "%nginx_branch ${CIRCLE_BRANCH}" >> rpmmacros',
            }
        },
        {
            "run": {
                "name": "Set up %plesk macro if passed by a job",
                "command": command_set_nginx_macros,
            }
        },
        {
            "run": {
                "name": "Run script to cleanup spec files that don't need rebuilding",
                "command": command_spec_files_cleanup,
            }
        },
    ]

build_steps += [
    {
        "run": {
            "name": "Run the build itself: this will do rpmlint and check RPMs existence among other things.",
            "command": "./utils/version-from-tag.sh && build" if self_mode else "build",
        }
    },
]
# Self mode skips store_test_results — verbatim template parity (no JUnit XML
# expected for single-spec tag-triggered builds).
if not self_mode:
    build_steps += [
        {
            "store_test_results": {
                "path": "/output/test-results",
            }
        },
    ]
build_steps += [
    {
        "run": {
            "name": "Check for RPM files and halt if none exist",
            "command": command_check_rpm_files_halt,
        }
    },
    {"persist_to_workspace": {"root": "/output", "paths": ["*.rpm"]}},
]

build_job_parameters = {
    "dist": {
        "description": "The dist tag of OS to build for",
        "type": "string",
    },
    "resource_class": {
        "description": "The resource class to use for the build",
        "type": "string",
        "default": resource_class,
    },
}

build_job_executor_parameters = {
    "name": "rpmbuilder",
    "dist": "<< parameters.dist >>",
}

rpmbuilder_executor_parameters = {
    "dist": {"type": "string"},
    "rpmlint": {"type": "integer", "default": 1},
}

rpmbuilder_executor_environment = {
    "RPMLINT": "<< parameters.rpmlint >>",
}

# Self mode omits enable_repos entirely (verbatim template parity); non-self
# repos always wire the standard enable_repos param/executor/env trio so that
# the existing per-branch overrides + check_packages_in_repo short-circuit work.
if not self_mode:
    build_job_parameters["enable_repos"] = {"type": "string", "default": ""}
    build_job_executor_parameters["enable_repos"] = "<< parameters.enable_repos >>"
    rpmbuilder_executor_parameters["enable_repos"] = {"type": "string", "default": ""}
    rpmbuilder_executor_environment["ENABLE_REPOS"] = "<< parameters.enable_repos >>"

if collection_name == "nginx":
    build_job_parameters["plesk"] = {
        "description": "Plesk major release version number, e.g. 18",
        "type": "integer",
        "default": 0,
    }
    build_job_parameters["mod"] = {
        "description": "Set to 1 to build NGINX-MOD-specific module as well",
        "type": "integer",
        "default": 0,
    }
    build_job_parameters["failure_tolerance"] = {
        "description": "Per-build failure tolerance fraction passed to rpmbuilder (e.g. '1.0' for ea4 to keep going through known-broken specs).",
        "type": "string",
        "default": "0.1",
    }
    build_job_executor_parameters["plesk"] = "<< parameters.plesk >>"
    build_job_executor_parameters["mod"] = "<< parameters.mod >>"
    build_job_executor_parameters["failure_tolerance"] = (
        "<< parameters.failure_tolerance >>"
    )
    rpmbuilder_executor_parameters["plesk"] = {"type": "integer", "default": 0}
    rpmbuilder_executor_parameters["mod"] = {"type": "integer", "default": 0}
    rpmbuilder_executor_parameters["failure_tolerance"] = {
        "type": "string",
        "default": "0.1",
    }
    rpmbuilder_executor_environment["PLESK"] = "<< parameters.plesk >>"
    rpmbuilder_executor_environment["MOD"] = "<< parameters.mod >>"
    rpmbuilder_executor_environment["FAILURE_TOLERANCE"] = (
        "<< parameters.failure_tolerance >>"
    )


circleci_config = {
    "version": 2.1,
    "executors": {
        "deploy": {
            "parameters": {"dist": {"type": "string"}, "arch": {"type": "string"}},
            "docker": [{"image": "kroniak/ssh-client"}],
            "working_directory": "/output",
            "environment": {
                "DISTRO": "<< parameters.dist >>",
                "ARCH": "<< parameters.arch >>",
            },
        },
        "rpmbuilder": {
            "parameters": rpmbuilder_executor_parameters,
            "docker": [{"image": "getpagespeed/rpmbuilder:<< parameters.dist >>"}],
            "working_directory": "/sources",
            "environment": rpmbuilder_executor_environment,
        },
    },
    "jobs": {
        "build": {
            "parameters": build_job_parameters,
            "resource_class": "<< parameters.resource_class >>",
            "executor": build_job_executor_parameters,
            "steps": build_steps,
        },
        "deploy": {
            "parallelism": 1,
            "parameters": {
                "dist": {
                    "description": "The dist tag of OS to deploy for",
                    "type": "string",
                },
                "arch": {
                    "description": "The architecture to deploy for",
                    "type": "string",
                },
            },
            "executor": {
                "name": "deploy",
                "dist": "<< parameters.dist >>",
                "arch": "<< parameters.arch >>",
            },
            "steps": [
                {"attach_workspace": {"at": "/output"}},
                {
                    "add_ssh_keys": {
                        "fingerprints": [
                            "8c:a4:dd:2c:47:4c:63:aa:90:0b:e0:d6:15:be:87:82"
                        ]
                    }
                },
                {
                    "run": {
                        "name": "Create project upload directory",
                        "command": command_incoming_mkdir,
                    }
                },
                {
                    "run": {
                        "name": "Deploy all RPMs to GetPageSpeed repo.",
                        "command": command_deploy_all_rpms,
                    }
                },
                {
                    "run": {
                        "name": "Trigger Deploy Hook.",
                        "command": command_trigger_incoming_hook,
                    }
                },
            ],
        },
    },
    "workflows": {},
}

# Opt-in smoke job template. Only emitted into `jobs:` when the project's
# settings.yml carries a non-empty `post_deploy_smoke:` block. Keeps
# non-opting consumers' generated config byte-identical.
if post_deploy_smoke:
    circleci_config["jobs"]["smoke"] = {
        "parameters": {
            "dist": {
                "description": "The dist tag of OS to smoke-install on",
                "type": "string",
            },
            "arch": {
                "description": "Architecture (informational; surfaces in job name)",
                "type": "string",
            },
            "resource_class": {
                "description": "Resource class for the smoke runner",
                "type": "string",
                "default": "medium",
            },
        },
        "resource_class": "<< parameters.resource_class >>",
        "executor": {
            "name": "rpmbuilder",
            "dist": "<< parameters.dist >>",
        },
        "environment": {
            "DISTRO": "<< parameters.dist >>",
            "ARCH": "<< parameters.arch >>",
        },
        "steps": [
            "checkout",
            {
                "run": {
                    "name": "Post-deploy install smoke + crash probe",
                    "command": "bash scripts/smoke.sh",
                }
            },
        ],
    }

# Prepare workflows
workflows = {}


# Function to generate workflow names
def get_workflow_name(dist, version, branch, arch):
    # if this is the only branch, don't include it in the workflow name
    # note that brandh is a dictionary, so we need to get the key count
    if len(branches) == 1:
        return f"build-deploy-{dist}{version}-{arch}"
    return f"build-deploy-{dist}{version}-{branch}-{arch}"


# Generate workflows
distros = matrix_config.get("distros", {})


def get_build_job_name(dist, version, branch, arch):
    # if this is the only branch, don't include it in the job name
    if len(branches) == 1:
        return f"build-{dist}{version}-{arch}"
    return f"build-{dist}{version}-{branch}-{arch}"


def get_deploy_job_name(dist, version, branch, arch):
    # if this is the only branch, don't include it in the job name
    if len(branches) == 1:
        return f"deploy-{dist}{version}-{arch}"
    return f"deploy-{dist}{version}-{branch}-{arch}"


for distro_name, distro_info in distros.items():
    dist = distro_info.get("dist", distro_name)
    versions = distro_info.get("versions", [])
    for version in versions:
        # Per-version distro overrides — primarily the plesk branch axis:
        # matrix.yml's rhel.version_overrides.10.has_plesk=False excludes
        # el10-plesk workflows even though el10 ∈ only_dists: ["el*"].
        # matrix.json stores version_overrides keys as strings (e.g. "10")
        # since JSON has no integer keys; matrix.yml versions arrive as ints.
        # Look up by both for safety.
        version_overrides_all = distro_info.get("version_overrides", {})
        version_overrides = (
            version_overrides_all.get(version)
            or version_overrides_all.get(str(version))
            or {}
        )
        has_plesk = version_overrides.get(
            "has_plesk", distro_info.get("has_plesk", False)
        )
        for branch in branches:
            branch_config = branches[branch]
            # Skip plesk branch on distro versions that don't support Plesk
            # (e.g. el10). Mirrors generate_config.py:175 logic. Keyed on the
            # channel name, not git_branch: a standalone Plesk repo such as
            # sw-nginx-compat builds the plesk channel from its master branch
            # (git_branch: master) and must still drop el10 (2026-09-02).
            if "plesk" in (branch, branch_config.get("git_branch")) and not has_plesk:
                continue
            # if only_dists list is present in branch_config, compare each element as wildcard "*" against current dist
            # and if matches, skip this distro

            if "only_dists" in branch_config:
                if not any(
                    fnmatch.fnmatch(f"{dist}{version}", pattern)
                    for pattern in branch_config["only_dists"]
                ):
                    continue
            # Per-branch resource_class overrides (opt-in, default-off).
            # Apply to the BUILD job only — smoke keeps its current hard-coded
            # medium/arm.medium so this contributes zero diff to consumers that
            # do not set these keys. When unset, branch_config.get returns
            # None and the existing emit path is preserved exactly.
            branch_rc = branch_config.get("resource_class")  # x86_64 build override
            branch_arm_rc = branch_config.get(
                "arm_resource_class"
            )  # aarch64 build override

            for arch in archs:
                # Skip architectures that are not supported
                if arch == "aarch64" and not distro_info.get("has_aarch64", True):
                    continue
                # branch config
                if "only_archs" in branch_config:
                    if not any(
                        fnmatch.fnmatch(arch, pattern)
                        for pattern in branch_config["only_archs"]
                    ):
                        continue
                # check if this distro and arch has been excluded
                # exclude: config can either have exclude: el or el7 or exclude: el7-x86_64 items
                # check excludes with wildcard support (e.g., "*", "el*", "amzn*-aarch64")
                combo_values = [dist, f"{dist}{version}", f"{dist}{version}-{arch}"]
                if any(
                    fnmatch.fnmatch(value, pattern)
                    for value in combo_values
                    for pattern in exclude_patterns
                ):
                    continue
                # If a `dists:` allowlist is set, drop anything that doesn't match it.
                if dists_allowlist and not any(
                    fnmatch.fnmatch(value, pattern)
                    for value in combo_values
                    for pattern in dists_allowlist
                ):
                    continue

                workflow_name = get_workflow_name(dist, version, branch, arch)
                build_job_name = get_build_job_name(dist, version, branch, arch)
                deploy_job_name = get_deploy_job_name(dist, version, branch, arch)

                # The branch filter and `git_branch` mapping in matrix.json are
                # not always the same (e.g. nginx "stable" → master, varnish
                # "varnish60" → master). Filter on the actual git branch name.
                # A project may publish into a collection channel whose key is
                # not its actual Git branch. Keep the collection key for
                # workflow naming and enable_repos, but allow the project to
                # override only the branch filter. This is deliberately valid
                # for one selected collection branch, avoiding an ambiguous
                # one-value-to-many-branches mapping.
                git_branch = git_branch_override or branch_config.get(
                    "git_branch", branch
                )
                only_branches = [git_branch]
                # if git branch is "master", "main", or "stable", treat them
                # as interchangeable so the workflow fires from any of them.
                main_branches = ["main", "master", "stable"]
                if git_branch in main_branches:
                    only_branches = main_branches

                # Build job parameters
                build_job = {
                    "build": {
                        "name": build_job_name,
                        # Least-privilege contexts (2026-08-30): a build job gets the
                        # repo-READ tokens only. The package-repo PUBLISH credentials
                        # live in `deploy`, which myci refuses to inject on any runner
                        # not marked `trusted: true` — four of the six runners are
                        # shared customer production boxes.
                        "context": "build-deps",
                        "dist": f"{dist}{version}",
                        "filters": {"branches": {"only": only_branches}},
                    }
                }
                # Set enable_repos so check_packages_in_repo (in rpmbuilder image)
                # can see prior builds in the channel where the artifact lives,
                # and short-circuit re-builds of an already-published NVR.
                # Convention: matrix.yml `collections.<X>.branches.<Y>` key is
                # both the sub-channel suffix and (unless `git_branch:` overrides)
                # the git branch name. "stable" is the canonical no-sub-channel case.
                # A branch may carry `enable_repos:` to override the conventional
                # repo id (e.g. freenginx-mainline ships as
                # [getpagespeed-freenginx-mainline]); null suppresses emission.
                if enable_repos_override:
                    build_job["build"]["enable_repos"] = enable_repos_override
                elif collection_name and branch != "stable":
                    enable_repos = branch_config.get(
                        "enable_repos", f"getpagespeed-extras-{branch}"
                    )
                    if enable_repos:
                        build_job["build"]["enable_repos"] = enable_repos

                # nginx collection: per-branch job-param overrides from matrix.json.
                # plesk_version → `plesk: <ver>` (verbatim parity for the plesk branch).
                # failure_tolerance → `failure_tolerance: '<frac>'` (e.g. ea4 = '1.0').
                # `mod` intentionally not surfaced per-job: nginx-mod cohort retired
                # (ABI-compatible with stable); standalone variant repos inherit
                # the executor default 0 via param wiring above.
                if collection_name == "nginx":
                    if "plesk_version" in branch_config:
                        build_job["build"]["plesk"] = branch_config["plesk_version"]
                    if "failure_tolerance" in branch_config:
                        build_job["build"]["failure_tolerance"] = branch_config[
                            "failure_tolerance"
                        ]

                # Add extra parameters for 'aarch64'
                if arch == "aarch64":
                    build_job["build"]["resource_class"] = (
                        branch_arm_rc or arm_resource_class
                    )
                elif branch_rc and branch_rc != resource_class:
                    # x86_64 normally inherits via the job parameter default;
                    # only emit an inline override when the branch differs
                    # from project-wide. Keeps non-opting branches byte-identical.
                    build_job["build"]["resource_class"] = branch_rc

                deploy_job = {
                    "deploy": {
                        "name": deploy_job_name,
                        # Publish rights: trusted runners only. See the build job above.
                        "context": "deploy",
                        "dist": f"{dist}{version}",
                        "arch": arch,
                        "filters": {"branches": {"only": only_branches}},
                        "requires": [build_job_name],
                    }
                }

                # If the actual Git branch is master, accept main as an alias.
                # A standalone project may map the default matrix key
                # ("master") to a version channel such as php84; do not let
                # that internal key silently enable deploys from main.
                if branch == "master" and git_branch == "master":
                    deploy_job["deploy"]["filters"]["branches"]["only"].append("main")

                # Self mode replaces branch-based filters with tag-based ones:
                # build fires for any tag, deploy only for /^v.*/ tags and never
                # for plain branch pushes. Verbatim parity with the retired
                # generated_config_self.yml template.
                if self_mode:
                    build_job["build"]["filters"] = {"tags": {"only": "/.*/"}}
                    deploy_job["deploy"]["filters"] = {
                        "branches": {"ignore": "/.*/"},
                        "tags": {"only": "/^v.*/"},
                    }

                # Construct the workflow
                workflows[workflow_name] = {"jobs": [build_job, deploy_job]}

                # Opt-in post-deploy smoke job. Default-off: if the project's
                # settings.yml has no post_deploy_smoke block, nothing is
                # appended and the workflow stays byte-identical for that
                # consumer. When opted in for this (branch, dist, arch), chain
                # a smoke job after deploy so it pulls the just-published RPM
                # from the channel and exercises a real install + crash probe
                # (see consumer-repo scripts/smoke.sh for the body).
                smoke_for_branch = post_deploy_smoke.get(branch)
                if smoke_for_branch:
                    smoke_dists = smoke_for_branch.get("dists", [])
                    smoke_archs = smoke_for_branch.get("archs", [])
                    if f"{dist}{version}" in smoke_dists and arch in smoke_archs:
                        if len(branches) == 1:
                            smoke_job_name = f"smoke-{dist}{version}-{arch}"
                        else:
                            smoke_job_name = f"smoke-{dist}{version}-{branch}-{arch}"
                        smoke_rc = arm_resource_class if arch == "aarch64" else "medium"
                        smoke_job = {
                            "smoke": {
                                "name": smoke_job_name,
                                # Installs the just-published package; needs repo-read
                                # auth only, never publish rights.
                                "context": "build-deps",
                                "dist": f"{dist}{version}",
                                "arch": arch,
                                "resource_class": smoke_rc,
                                "filters": {"branches": {"only": only_branches}},
                                "requires": [deploy_job_name],
                            }
                        }
                        workflows[workflow_name]["jobs"].append(smoke_job)

# Add the generated workflows to the CircleCI config
circleci_config["workflows"].update(workflows)

# Write the CircleCI config
circleci_dir = os.path.join(project_dir, ".circleci")
os.makedirs(circleci_dir, exist_ok=True)
config_file = os.path.join(circleci_dir, "config.yml")

with open(config_file, "w") as f:
    yaml.dump(circleci_config, f)

print(f"CircleCI configuration generated at {config_file}")
