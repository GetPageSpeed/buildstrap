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
    # array of OS releases, of course we build against the current version always:
    distros[distro]['versions'] = [
        distro_version
    ]
    # build against that many past releases of OS
    os_versions = 2
    if 'os_versions' in distro_config:
        os_versions = int(distro_config['os_versions'])
    # now add past release of the OS:
    for i in range(1, os_versions):
        distros[distro]['versions'].append(distro_version - i)


# which virtual NGINX branch builds on which git branch
nginx_branches = {
    'stable': 'master',
    'mainline': 'mainline',
    'plesk': 'plesk',
    # spec is always equal to stable (with mod conditionals), but we only push those modules which need unique version
    # due to SSL requirements, etc.
    # nginx-mod for modules only needed for e.g. EL7 where we built against non-system OpenSSL, but not in EL8, etc.
    'nginx-mod': 'nginx-mod'
}

# for standard RPM spec repo
config = {
    'workflows': {}
}

# for nginx module RPM spec repo
config_nginx = {
    'workflows': {}
}

# for nginx module RPM spec repo that is already built by Plesk itself
config_nginx_without_plesk = {
    'workflows': {}
}

# for software source repo with the RPM spec file, e.g. fds
config_self = {
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
            if git_branch == 'plesk':
                if 'has_plesk' not in distro_config or not distro_config['has_plesk']:
                    continue
            if git_branch == 'nginx-mod' and dist != 'el7':
                continue
            workflow_name = f"build-deploy-{dist}-{nginx_branch}"
            workflow = {
                'jobs': [
                    {
                        'build': {
                            'name': f"{distro_build_job_name}-{nginx_branch}",
                            'dist': dist,
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

            config_nginx['workflows'][workflow_name] = workflow

            if git_branch == 'nginx-mod':
                workflow['jobs'][0]['build']['mod'] = 1

            if git_branch == 'plesk':
                workflow['jobs'][0]['build']['plesk'] = 18
            else:
                config_nginx_without_plesk['workflows'][workflow_name] = workflow

        config_self['workflows'][f"build-deploy-{dist}"] = {
            'jobs': [
                {
                    'build': {
                        'name': distro_build_job_name,
                        'dist': dist,
                        # required since `deploy` has tag filters AND requires `build`
                        'filters': {
                            'tags': {
                                'only': '/.*/'
                            }
                        }
                    }
                },
                {
                    'deploy': {
                        'name': distro_deploy_job_name,
                        'dist': dist,
                        'context': 'org-global',
                        'requires': [
                            distro_build_job_name
                        ],
                        'filters': {
                            'tags': {
                                'only': '/^v.*/'
                            },
                            'branches': {
                                'ignore': '/.*/'
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

# the same for branch-based NGINX builds where Plesk already ships its own module
shutil.copy('partial_config_nginx.yml', 'generated_config_nginx_without_plesk.yml')
with open('generated_config_nginx_without_plesk.yml', 'a') as f:
    yaml.dump(config_nginx_without_plesk, f, default_flow_style=None)

shutil.copy('partial_config_self.yml', 'generated_config_self.yml')
with open('generated_config_self.yml', 'a') as f:
    yaml.dump(config_self, f, default_flow_style=None)
