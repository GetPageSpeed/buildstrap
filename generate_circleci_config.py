#!/usr/bin/env python3
import os
import argparse
import json
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import LiteralScalarString, FoldedScalarString

# Initialize YAML handler
yaml = YAML()
yaml.default_flow_style = False

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
        project_settings = yaml.safe_load(f)
else:
    project_settings = {}

# Default architectures
default_archs = ["x86_64", "aarch64"]

# Get architectures from settings.yml or default to the default_archs
archs = project_settings.get("archs", default_archs)
# if there is only one .spec file in the directory, look for "BuildArch:      noarch" in it
# if found, set only "noarch" to the list of archs
if len([f for f in os.listdir(project_dir) if f.endswith(".spec")]) == 1:
    spec_file = os.path.join(
        project_dir, [f for f in os.listdir(project_dir) if f.endswith(".spec")][0]
    )
    # scan and split the spec file by lines
    with open(spec_file, "r") as f:
        spec_lines = f.readlines()
        # look for "BuildArch: noarch" in the spec file
        for line in spec_lines:
            # remove dup1cqte spaces and check if the line contains "BuildArch: noarch"
            line = line.strip().replace(" ", "")
            if "BuildArch:noarch" in line:
                archs = ["noarch"]
                break
exclude_archs = project_settings.get("exclude_archs", [])

# Exclude architectures
archs = [arch for arch in archs if arch not in exclude_archs]

# Read matrix.json
matrix_file = os.path.join(os.path.dirname(__file__), "matrix.json")
with open(matrix_file, "r") as f:
    matrix_config = json.load(f)

# Get the branches from matrix.json "collections": { "nginx": { "branches": {
# what branches depends on detected collection, e.g. "nginx"
branches = {
    "master": {
        "description": "Main release branch",
    }
}
collection_name = None
# if project diredtory nqme starts with "nginx-", set collection_name to "nginx"
if project_dir.startswith("nginx-"):
    collection_name = "nginx"
if collection_name:
    branches = matrix_config["collections"][collection_name]["branches"]

resource_class = "medium"
# if only noarch, fine with small
if len(archs) == 1 and "noarch" in archs:
    resource_class = "small"

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
else
  echo "No RPM files found. Halting the job."
  curl --request POST --url https://circleci.com/api/v2/workflow/$CIRCLE_WORKFLOW_ID/cancel --header "Circle-Token: ${CIRCLE_TOKEN}"
  circleci-agent step halt
fi"""
)

command_incoming_mkdir = FoldedScalarString(
    "ssh -o StrictHostKeyChecking=no "
    "$GPS_BUILD_USER@$GPS_BUILD_SERVER "
    '"mkdir -p ~/incoming/${CIRCLE_PROJECT_REPONAME}/${DISTRO}/${ARCH}/${CIRCLE_BRANCH}"'  # this way quotoing is important otherwise ~ resolves on local machine to /root
)

command_deploy_all_rpms = FoldedScalarString(
    "ls -al *.rpm & scp -o StrictHostKeyChecking=no -q -r *.rpm "
    "$GPS_BUILD_USER@$GPS_BUILD_SERVER:~/incoming/${CIRCLE_PROJECT_REPONAME}/${DISTRO}/${ARCH}/${CIRCLE_BRANCH}/"
)

command_trigger_incoming_hook = FoldedScalarString(
    "ssh -o StrictHostKeyChecking=no -q $GPS_BUILD_USER@$GPS_BUILD_SERVER"
    ' "nohup ~/scripts/incoming.sh ${CIRCLE_PROJECT_REPONAME}/${DISTRO}/${ARCH}/${CIRCLE_BRANCH}/ > ~/incoming/$CIRCLE_PROJECT_REPONAME/$DISTRO/${ARCH}/${CIRCLE_BRANCH}/process.log 2>&1&"'
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
            "command": "build",
        }
    },
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
    "enable_repos": {"type": "string", "default": ""},
    "resource_class": {
        "description": "The resource class to use for the build",
        "type": "string",
        "default": resource_class,
    },
}

build_job_executor_parameters = {
    "name": "rpmbuilder",
    "dist": "<< parameters.dist >>",
    "enable_repos": "<< parameters.enable_repos >>",
}

rpmbuilder_executor_parameters = {
    "dist": {"type": "string"},
    "rpmlint": {"type": "integer", "default": 1},
    "enable_repos": {"type": "string", "default": ""},
}

rpmbuilder_executor_environment = {
    "RPMLINT": "<< parameters.rpmlint >>",
    "ENABLE_REPOS": "<< parameters.enable_repos >>",
}

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
    build_job_executor_parameters["plesk"] = "<< parameters.plesk >>"
    build_job_executor_parameters["mod"] = "<< parameters.mod >>"
    rpmbuilder_executor_parameters["plesk"] = {"type": "integer", "default": 0}
    rpmbuilder_executor_parameters["mod"] = {"type": "integer", "default": 0}
    rpmbuilder_executor_environment["PLESK"] = "<< parameters.plesk >>"
    rpmbuilder_executor_environment["MOD"] = "<< parameters.mod >>"


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
                        "name": "Ensure project specific upload directory to avoid deploy collisions",
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

# Prepare workflows
workflows = {}


# Function to generate workflow names
def get_workflow_name(dist, version, branch, arch):
    # if this is the only branch, don't include it in the workflow name
    # note that brandh is a dictionary, so we need to get the key count
    print(len(branches))
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
        for branch in branches:
            for arch in archs:
                # Skip architectures that are not supported
                if arch == "aarch64" and not distro_info.get("has_aarch64", True):
                    continue
                workflow_name = get_workflow_name(dist, version, branch, arch)
                build_job_name = get_build_job_name(dist, version, branch, arch)
                deploy_job_name = get_deploy_job_name(dist, version, branch, arch)

                # Build job parameters
                build_job = {
                    "build": {
                        "name": build_job_name,
                        "context": "org-global",
                        "dist": f"{dist}{version}",
                        "filters": {"branches": {"only": [branch]}},
                    }
                }

                # Add extra parameters for 'aarch64'
                if arch == "aarch64":
                    build_job["build"]["resource_class"] = "arm.medium"

                deploy_job = {
                    "deploy": {
                        "name": deploy_job_name,
                        "context": "org-global",
                        "dist": f"{dist}{version}",
                        "arch": arch,
                        "filters": {"branches": {"only": [branch]}},
                        "requires": [build_job_name],
                    }
                }

                # Construct the workflow
                workflows[workflow_name] = {"jobs": [build_job, deploy_job]}

# Add the generated workflows to the CircleCI config
circleci_config["workflows"].update(workflows)

# Write the CircleCI config
circleci_dir = os.path.join(project_dir, ".circleci")
os.makedirs(circleci_dir, exist_ok=True)
config_file = os.path.join(circleci_dir, "config.yml")

with open(config_file, "w") as f:
    yaml.dump(circleci_config, f)

print(f"CircleCI configuration generated at {config_file}")
