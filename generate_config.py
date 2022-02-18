#!/usr/bin/env python3
# fetch latest release number for each OS and populate "releases" key in matrix.yml
# with current and previous release numbers (building for last two major)
import lastversion
import yaml
import os

abspath = os.path.abspath(__file__)
os.chdir(os.path.dirname(abspath))
with open("matrix.yml", 'r') as f:
    try:
        distros_config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        print(exc)
        exit(1)

distros = distros_config['distros']
for distro, distro_config in distros.items():
    if 'versions_check' in distro_config and not distro_config['versions_check']:
        continue
    distro_version = lastversion.latest(distro).release[0]
    # print(f"{distro}'s latest major version is {distro_version}")
    distros[distro]['versions'] = [
        distro_version - 1,
        distro_version
    ]
# print(distros)

# build up final yml based on partial_config.yml and set up workflows: only
with open('partial_config.yml') as f:
    config = yaml.safe_load(f)
    config['workflows'] = {}
    for distro_name, distro_config in distros.items():
        for v in distro_config['versions']:
            print(f"Generating {v} for {distro_name}")
            dist = distro_name
            if 'dist' in distro_config:
                dist = distro_config['dist']
            dist = dist + str(v)

            distro_build_job_name = f"build-{dist}"
            distro_deploy_job_name = f"deploy-{dist}"

            config['workflows'][f"build-deploy-{dist}"] = [
                {
                    'build': {
                        'name': distro_build_job_name,
                        'dist': dist
                    },
                    'deploy': {
                        'name': distro_deploy_job_name,
                        'dist': dist,
                        'context': 'org-global',
                        'requires': [
                            distro_build_job_name
                        ]
                    }
                }
            ]

with open('generated_config.yml', 'w') as f:
    yaml.dump(config, f) #, default_flow_style=None)
