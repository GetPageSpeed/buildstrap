#!/bin/bash
# mapping of dists to directories:
declare -A dists=(
  ["el7"]="redhat/7"
  ["el8"]="redhat/8"
  ["el9"]="redhat/9"
  ["fc39"]="fedora/39"
  ["fc38"]="fedora/38"
  ["fc40"]="fedora/40"
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
