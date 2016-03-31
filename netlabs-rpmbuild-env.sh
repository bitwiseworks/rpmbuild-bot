#!/bin/sh

# This script sets up the environment necessary to build official RPM packages
# for OS/2 and eComStation distributed through the netlabs.org RPM repositories.

# Use English messages
export LANG=en_US

# Use RPM ash
export SHELL=`rpm --eval '%_bindir'`/sh.exe     # general shell
export EMXSHELL=$SHELL                          # LIBC shell
export CONFIG_SHELL=$SHELL                      # configure shell
export MAKESHELL=$SHELL                         # make shell
export EXECSHELL=$SHELL                         # perl shell

# Reset common vars (for consistency)
export CFLAGS=
export CXXFLAGS=
export FFLAGS=
export LDFLAGS=

# Variables for netlabs scripts
NETLABS_RPM_ARCH_LIST="pentium4 i686"      # last arch is used for ZIP
