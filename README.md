# buildstrap

Bootstrap generation of CircleCI config.yml for RPM builds of the latest OS releases.
Creates `generated_config.yml` that builds and deploys RPM against supported OS versions.

## Setup

The buildstrap automatically updates `generated_config.yml` by running `~/buildstrap/cron.sh` on
the GetPageSpeed build server.

The list of operating systems supported can be updated in `matrix.yml`.
The `rpmbuilder` images are tagged based on expected RPM dist tag of an operating system, e.g.
`getpagespeed/rpmbuilder:amzn2`.

So `matrix.yml` file simply specifies the operating system label as understood by `lastversion` and
their corresponding dist tag in order to build against the correct `rpmbuilder` image.

## Usage in a spec project repository

Refer to [`wrk` example](https://github.com/GetPageSpeed/wrk-rpm/blob/master/.circleci/config.yml).
In the static config.yml we use `setup: true` and fetch the recent `generated_config.yml`.

In this way, whenever the software is updated (GitHub repo is changed), we build it against desired
set of operating systems.

However, when a new operating system is released, there is no automatic rebuild of the package.
This can be implemented by triggering CircleCi workflows for individual spec projects using API.

For a software that is regularly updated, triggering workflows isn't required...

