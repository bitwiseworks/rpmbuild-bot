#!/bin/sh

# This script is a wrapper around rpmbuild that uses the environment from the 
# netlabs_rpmbuild_env.sh script (see that file for more information about
# the environment). It passes all arguments to rpmbuild, unchanged.

# Set up the official netlabs.org rpmbuild environment
. netlabs-rpmbuild-env.sh

rpmbuild "$@"
