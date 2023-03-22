#!/bin/bash
# mapping of dists to directories:
declare -A dists=(
  ["el7"]="redhat/7"
  ["el8"]="redhat/8"
  ["el9"]="redhat/9"
  ["fc37"]="fedora/37"
  ["fc36"]="fedora/36"
  ["amzn2"]="amzn/2"
  ["amzn2023"]="amzn/2023"
  ["sles15"]="sles/15"
)
# mapping of directories to full descriptive names:
declare -A os_long=(
  ["redhat"]="CentOS/RHEL"
  ["fedora"]="Fedora Linux"
  ["amzn"]="Amazon Linux"
  ["sles"]="SUSE Linux Enterprise"
)
