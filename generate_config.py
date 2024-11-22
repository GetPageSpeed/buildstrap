#!/usr/bin/env python3
"""
Fetch the latest release number for each OS and populate "releases" key in matrix.yml
with current and previous release numbers (building for last two major)
"""
import copy
import shutil
import stat
import json

import lastversion
import yaml
import os

abspath = os.path.abspath(__file__)
os.chdir(os.path.dirname(abspath))
with open("matrix.yml", "r") as f:
    try:
        distros_config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        print(exc)
        exit(1)

distros = distros_config["distros"]
for distro, distro_config in distros.items():
    if "dir" not in distro_config:
        distro_config["dir"] = distro_config["dist"]
    if "versions_check" in distro_config and not distro_config["versions_check"]:
        continue
    distro_version = lastversion.latest(distro).release[0]
    # print(f"{distro}'s latest major version is {distro_version}")
    # array of OS releases, of course we build against the current version always:
    distros[distro]["versions"] = [distro_version]
    # build against that many past releases of OS
    os_versions = distro_config.get(
        "os_versions", distros_config["distro_defaults"]["os_versions"]
    )

    # now add past release of the OS:
    for i in range(1, os_versions):
        distros[distro]["versions"].append(distro_version - i)

    if distro_config.get("include_rolling_release", False):
        distros[distro]["versions"].append(distro_version + 1)

# which virtual NGINX branch builds on which git branch
nginx_branches = {
    "stable": "master",
    "mainline": "mainline",
    "plesk": "plesk",
    # spec is always equal to stable (with mod conditionals), but we only push those modules which need unique version
    # due to SSL requirements, etc.
    # nginx-mod for modules only needed for e.g. EL7 where we built against non-system OpenSSL, but not in EL8, etc.
    "nginx-mod": "nginx-mod",
    "nginx-quic": "nginx-quic",
    "angie": "angie",
}

# for standard RPM spec repo
config = {"workflows": {}}

# for nginx module RPM spec repo
config_nginx = {"workflows": {}}

# for nginx module RPM spec repo that is already built by Plesk itself
config_nginx_without_plesk = {"workflows": {}}

# for software source repo with the RPM spec file, e.g. fds
config_self = {"workflows": {}}

# build up extra yml fpr appending to partial_config.yml
# we only construct workflows:


def get_workflow(dist, arch, tags_only=False):
    workflow = {
        "jobs": [
            {
                "build": {
                    "name": f"build-{dist}-{arch}",
                    "dist": dist,
                    "context": "org-global",
                    # required since `deploy` has tag filters AND requires `build`
                    "filters": {"tags": {"only": "/.*/"}},
                }
            },
            {
                "deploy": {
                    "name": f"deploy-{dist}-{arch}",
                    "dist": dist,
                    "arch": arch,
                    "context": "org-global",
                    "requires": [f"build-{dist}-{arch}"],
                    "filters": {
                        "tags": {"only": "/^v.*/"},
                        "branches": {"ignore": "/.*/"},
                    },
                }
            },
        ]
    }
    if not tags_only:
        # remove the filters
        workflow["jobs"][0]["build"].pop("filters")
        workflow["jobs"][1]["deploy"].pop("filters")
    # if we are building for aarch64, we need to specify resource_class for the build job
    if arch == "aarch64":
        workflow["jobs"][0]["build"]["resource_class"] = "arm.medium"
    return workflow


def get_nginx_workflow(dist, git_branch, nginx_branch, arch):
    workflow = {
        "jobs": [
            {
                "build": {
                    "name": f"build-{dist}-{nginx_branch}-{arch}",
                    "dist": dist,
                    "context": "org-global",
                    "filters": {"branches": {"only": git_branch}},
                },
            },
            {
                "deploy": {
                    "name": f"deploy-{dist}-{nginx_branch}-{arch}",
                    "dist": dist,
                    "arch": arch,
                    "context": "org-global",
                    "requires": [
                        f"build-{dist}-{nginx_branch}-{arch}",
                    ],
                    "filters": {"branches": {"only": git_branch}},
                }
            },
        ]
    }

    if git_branch == "mainline":
        workflow["jobs"][0]["build"]["enable_repos"] = "getpagespeed-extras-mainline"

    if git_branch == "nginx-mod":
        workflow["jobs"][0]["build"]["mod"] = 1

    if git_branch == "plesk":
        workflow["jobs"][0]["build"]["plesk"] = 18
        workflow["jobs"][0]["build"]["enable_repos"] = "getpagespeed-extras-plesk"

    # if we are building for aarch64, we need to specify resource_class for the build job
    if arch == "aarch64":
        workflow["jobs"][0]["build"]["resource_class"] = "arm.medium"
    return workflow


for distro_name, distro_config in distros.items():
    for v in distro_config["versions"]:
        print(f"Generating {v} for {distro_name}")
        dist = distro_name
        if "dist" in distro_config:
            dist = distro_config["dist"]
        dist = dist + str(v)

        distro_build_job_name = f"build-{dist}"
        distro_deploy_job_name = f"deploy-{dist}"

        config["workflows"][f"build-deploy-{dist}-x86_64"] = get_workflow(
            dist, "x86_64"
        )
        config["workflows"][f"build-deploy-{dist}-aarch64"] = get_workflow(
            dist, "aarch64"
        )

        for nginx_branch, git_branch in nginx_branches.items():
            if git_branch == "plesk":
                if "has_plesk" not in distro_config or not distro_config["has_plesk"]:
                    continue
            if git_branch == "nginx-mod" and dist != "el7":
                continue

            config_nginx["workflows"][f"build-deploy-{dist}-{nginx_branch}-x86-64"] = (
                get_nginx_workflow(dist, git_branch, nginx_branch, "x86_64")
            )

            if git_branch != "plesk":
                # add aarch64 build for all branches except plesk
                config_nginx["workflows"][
                    f"build-deploy-{dist}-{nginx_branch}-aarch64"
                ] = get_nginx_workflow(dist, git_branch, nginx_branch, "aarch64")
                config_nginx_without_plesk["workflows"][
                    f"build-deploy-{dist}-{nginx_branch}-x86-64"
                ] = config_nginx["workflows"][
                    f"build-deploy-{dist}-{nginx_branch}-x86-64"
                ].copy()
                config_nginx_without_plesk["workflows"][
                    f"build-deploy-{dist}-{nginx_branch}-aarch64"
                ] = config_nginx["workflows"][
                    f"build-deploy-{dist}-{nginx_branch}-aarch64"
                ].copy()

        config_self["workflows"][f"build-deploy-{dist}-x86_64"] = get_workflow(
            dist, "x86_64", tags_only=True
        )
        config_self["workflows"][f"build-deploy-{dist}-aarch64"] = get_workflow(
            dist, "aarch64", tags_only=True
        )

# copy this raw yml to preserve formatting
shutil.copy("partial_config.yml", "generated_config.yml")
with open("generated_config.yml", "a") as f:
    yaml.dump(config, f, default_flow_style=None)

# the same for branch-based NGINX builds
shutil.copy("partial_config_nginx.yml", "generated_config_nginx.yml")
with open("generated_config_nginx.yml", "a") as f:
    yaml.dump(config_nginx, f, default_flow_style=None)

# the same for branch-based NGINX builds where Plesk already ships its own module
shutil.copy("partial_config_nginx.yml", "generated_config_nginx_without_plesk.yml")
with open("generated_config_nginx_without_plesk.yml", "a") as f:
    yaml.dump(config_nginx_without_plesk, f, default_flow_style=None)

shutil.copy("partial_config_self.yml", "generated_config_self.yml")
with open("generated_config_self.yml", "a") as f:
    yaml.dump(config_self, f, default_flow_style=None)

# write generated_config_self_only_specs which will have filters for every job, on specs branch
shutil.copy("partial_config.yml", "generated_config_specs_only.yml")
# set filters on branch to "specs" for every job
config_specs_only = copy.deepcopy(config)
for workflow in config_specs_only["workflows"]:
    for entry in config_specs_only["workflows"][workflow]["jobs"]:
        job_name = list(entry.keys())[0]
        job = entry[job_name]
        if "filters" not in job:
            job["filters"] = {}
        if "branches" not in job["filters"]:
            job["filters"]["branches"] = {}
        job["filters"]["branches"]["only"] = "specs"
with open("generated_config_specs_only.yml", "a") as f:
    yaml.dump(config_specs_only, f, default_flow_style=None)

# write helper bash arrays for shell scripts
# declare -A dists=(
#   ["el6"]="redhat/6"
#   ["el7"]="redhat/7"
#   ["el8"]="redhat/8"
#   ["el9"]="redhat/9"
#   ["fc33"]="fedora/33"
#   ["fc34"]="fedora/34"
#   ["fc35"]="fedora/35"
#   ["fc36"]="fedora/36"
#   ["fc37"]="fedora/37"
#   ["amzn2"]="amzn/2"
#   ["sles15"]="sles/15"
# )
dists_array_s_list = [
    "#!/bin/bash",
    "# auto-generated by generate_config.py from matrix.yml",
    "# mapping of dists to directories:",
    "declare -A dists=(",
]
for distro_name, distro_config in distros.items():
    for v in distro_config["versions"]:
        line_fmt = '  ["{}"]="{}"'
        dists_array_s_list.append(
            line_fmt.format(
                f"{distro_config['dist']}{v}", f"{distro_config['dir']}/{v}"
            )
        )
dists_array_s_list.append(")")

dists_array_s_list.append("# mapping of directories to full descriptive names:")
# declare -A os_long=( ["redhat"]="CentOS/RHEL" ["amzn"]="Amazon Linux" ["fedora"]="Fedora Linux" )
dists_array_s_list.append("declare -A os_long=(")
for distro_name, distro_config in distros.items():
    line_fmt = '  ["{}"]="{}"'
    dists_array_s_list.append(
        line_fmt.format(f"{distro_config['dir']}", f"{distro_config['description']}")
    )
dists_array_s_list.append(")")
dists_array_s_list.append("")

with open("matrix.sh", "w") as f:
    f.write("\n".join(dists_array_s_list))

st = os.stat("matrix.sh")
os.chmod("matrix.sh", st.st_mode | stat.S_IEXEC)

# Generate matrix.json which is useful for Repo Explorer feature at GetPageSpeed
# Same as matrix.yml but in json format and with filled out versions
# Just json dump distros variables
with open("matrix.json", "w", encoding="utf-8") as f:
    json.dump(distros_config, f, indent=4)

# If rpmbuilder directory exists, cd into it, otherwise exit with error
if not os.path.exists("../rpmbuilder"):
    print("rpmbuilder directory not found")
    exit(1)

# cd into rpmbuilder directory
os.chdir("../rpmbuilder")

# Also write matrix.json for the rpmbuilder project
with open("matrix.json", "w", encoding="utf-8") as f:
    json.dump(distros_config, f, indent=4)

# Create special distro_versions.json for the rpmbuilder project
# Used in matrix: of its GitHub Actions workflow
distro_versions = []
for distro_name, distro_config in distros.items():
    for v in distro_config["versions"]:
        distro_versions.append({"os": distro_config["rpmbuilder_name"], "version": v})
with open("distro_versions.json", "w", encoding="utf-8") as f:
    json.dump({"include": distro_versions}, f, indent=4)

# Write defaults file as well:
default_lines = []
for distro_name, distro_config in distros.items():
    for v in distro_config["versions"]:
        default_lines.append(f"{distro_config['rpmbuilder_name']} {v}")
# write to defaults file at ../rpmbuilder/defaults
with open("defaults", "w", encoding="utf-8") as f:
    f.write("\n".join(default_lines))

print("Done")
