#!/usr/bin/env python3
# fetch latest release number for each OS and populate "releases" key in matrix.yml
# with current and previous release numbers (building for last two major)
import shutil

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

# which virtual NGINX branch builds on which git branch
nginx_branches = {
    'stable': 'master',
    'mainline': 'mainline',
    'plesk': 'plesk'
}

config = {
    'workflows': {}
}

config_nginx = {
    'workflows': {}
}

# build up extra yml fpr appending to partial_config.yml
# we only construct workflows:
for distro_name, distro_config in distros.items():
    for v in distro_config['versions']:
        print(f"Generating {v} for {distro_name}")
        dist = distro_name
        if 'dist' in distro_config:
            dist = distro_config['dist']
        dist = dist + str(v)

        distro_build_job_name = f"build-{dist}"
        distro_deploy_job_name = f"deploy-{dist}"

        config['workflows'][f"build-deploy-{dist}"] = {
            'jobs': [
                {
                    'build': {
                        'name': distro_build_job_name,
                        'dist': dist
                    },
                },
                {
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
        }

        for nginx_branch, git_branch in nginx_branches.items():
            config_nginx['workflows'][f"build-deploy-{dist}-{nginx_branch}"] = {
                'jobs': [
                    {
                        'build': {
                            'name': f"{distro_build_job_name}-{nginx_branch}",
                            'dist': dist,
                            'plesk': 18 if nginx_branch == 'plesk' else 0,
                            'filters': {
                                'branches': {
                                    'only': git_branch
                                }
                            }
                        },

                    },
                    {
                        'deploy': {
                            'name': f"{distro_deploy_job_name}-{nginx_branch}",
                            'dist': dist,
                            'context': 'org-global',
                            'requires': [
                                f"{distro_build_job_name}-{nginx_branch}",
                            ],
                            'filters': {
                                'branches': {
                                    'only': git_branch
                                }
                            }
                        }
                    }
                ]
            }

# copy this raw yml to preserve formatting
shutil.copy('partial_config.yml', 'generated_config.yml')
with open('generated_config.yml', 'a') as f:
    yaml.dump(config, f, default_flow_style=None)

# the same for branch-based NGINX builds
shutil.copy('partial_config_nginx.yml', 'generated_config_nginx.yml')
with open('generated_config_nginx.yml', 'a') as f:
    yaml.dump(config_nginx, f, default_flow_style=None)