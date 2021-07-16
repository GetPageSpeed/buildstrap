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
for distro in distros:
    distro_version = lastversion.latest(distro).release[0]
    # print(f"{distro}'s latest major version is {distro_version}")
    distros[distro]['versions'] = [
        distro_version - 1,
        distro_version
    ]
# print(distros)

# build up final yml based on header.yml and set up jobs: and workflows:
config = {
    'version': '2.1',
    'jobs': {},
    'workflows': {
        'version': 2
    }
}

with open('header.yml') as f:
    header_config = yaml.safe_load(f)
    for distro_name, distro_config in distros.items():
        for v in distro_config['versions']:
            print(f"Generating {v} for {distro_name}")
            dist = distro_name
            if 'dist' in distro_config:
                dist = distro_config['dist']
            distro_build_job_name = f"{dist}{v}"
            # distro_build_job = header_config['defaults']
            docker_tag_base = distro_name
            if 'docker-tag-base' in distro_config:
                docker_tag_base = distro_config['docker-tag-base']
            docker_image = f'getpagespeed/rpmbuilder:{docker_tag_base}-{v}'
            distro_build_job = header_config['defaults'].copy()
            distro_build_job.update({
                'docker': [{
                    'image': docker_image
                }]
            })
            config['jobs'][distro_build_job_name] = distro_build_job

            distro_deploy_job_name = f"deploy-{distro_build_job_name}"
            distro_deploy_job = header_config['deploy'].copy()
            distro_deploy_job['environment'] = {
                'DISTRO': distro_build_job_name
            }
            config['jobs'][distro_deploy_job_name] = distro_deploy_job

            distro_workflow_name = f"build-deploy-{distro_build_job_name}"
            distro_workflow = {'jobs': []}
            distro_workflow['jobs'].append(distro_build_job_name)
            distro_workflow['jobs'].append({
                distro_deploy_job_name: {
                    'context': 'org-global',
                    'requires': [
                        distro_build_job_name
                    ]
                }
            })
            config['workflows'][distro_workflow_name] = distro_workflow

with open('generated_config.yml', 'w') as f:
    yaml.dump(config, f, default_flow_style=None)
