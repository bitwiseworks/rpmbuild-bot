#!/bin/sh

#
# rpmbuild-bot-env.sh: RPM Build Bot distribtuion build environment example.
#
# Author: Dmitriy Kuminov <coding@dmik.org>
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#
# Synopsis
# --------
#
# This script sets up the environment necessary to build RPM packages with
# rpmbuild-bot.sh.
#
# NOTE: The values in this script are global and distribution channel specific
# settings (rather than site- or user-specific). They are only intended to be
# changed when the distribution rules change. All user-configurable
# site-specific options are located in a separate script named
# rpmbuild-bot-local.sh (which is executed when present in $HOME).
#

#
# Load local settings (if any).
#

local_env="${0%.sh}"
local_env="${local_env##*/}"
local_env="$HOME/${local_env%-env}-local.sh"
[ -f "$local_env" ] && . "$local_env"

#
# Default POSIX shell environment.
#

# Use English messages.
export LANG=en_US

# Use RPM ash.
export SHELL=`rpm --eval '%_bindir'`/sh.exe     # general shell
export EMXSHELL=$SHELL                          # LIBC shell
export CONFIG_SHELL=$SHELL                      # configure shell
export MAKESHELL=$SHELL                         # make shell
export EXECSHELL=$SHELL                         # perl shell

# Reset common vars (for consistency).
export CFLAGS=
export CXXFLAGS=
export FFLAGS=
export LDFLAGS=

#
# Default rpmbuild-bot conviguration.
#

# List of architectures to build (the last one is used for the ZIP
# package and for local test builds).
RPMBUILD_BOT_ARCH_LIST="pentium4 i686"

# Overrides of RPM arch list for specific packages.
# Note that dash symbols in package names should be replaced with underscores
# in the variables below (e.g. use RPMBUILD_BOT_ARCH_LIST_foo_bar for the
# foo-bar package).
RPMBUILD_BOT_ARCH_LIST_libc="i686" # Binary build -> no other archs.

# Basic RPM repository layout for this distribution channel.
RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_rpm="\$base/i386/\$arch"
RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_srpm="\$base/i386/SRPMS"
RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_zip="\$base/zip"

# List of repositories for "upload" command (the first one is the default).
RPMBUILD_BOT_UPLOAD_REPO_LIST="exp rel"

# Sanity checks.
check_dir_var "RPM_NETLABS_ORG_DIR"

# Directory in the local filesystem for each repository (usually mapped
# to a WEBAV resource).
RPMBUILD_BOT_UPLOAD_exp_DIR="$RPM_NETLABS_ORG_DIR/experimental/00"
RPMBUILD_BOT_UPLOAD_rel_DIR="$RPM_NETLABS_ORG_DIR/release/00"
