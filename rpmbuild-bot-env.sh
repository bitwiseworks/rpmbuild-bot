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
RPMBUILD_BOT_ARCH_LIST_kLIBCum="i686" # Binary build -> no other archs.
RPMBUILD_BOT_ARCH_LIST_klusrmgr="i686" # Binary build -> no other archs.
RPMBUILD_BOT_ARCH_LIST_os2tk45="i686" # Binary build -> no other archs.

# Legacy DLLs for specific packages. Each RPM from the list (format is
# "ABI|NAME|VERSION-RELEASE|[FILEMASK]|[ARCH]") for each target platform is
# downloaded from a repository specified in RPMBUILD_BOT_UPLOAD_REPO_STABLE
# and scanned for FILEMASK files (*.dll by default). These files are then
# extracted to a directory called RPM_SOURCE_DIR/PACKAGE-legacy (preserving the
# original directory tree) and, if PACKAGE.spec contains a macro named
# %legacy_runtime_packages, they are later placed to a sub-package called
# `legacy-ABI` when rpmbuild is run. If ARCH is specified, this platform's
# legacy package will be used for all target platforms.
RPMBUILD_BOT_LEGACY_libvpx="2|libvpx|1.4.0-2"
RPMBUILD_BOT_LEGACY_libtiff="4|libtiff-legacy|3.9.5-2"
RPMBUILD_BOT_LEGACY_xz="4|liblzma0|4.999.9beta-5"
RPMBUILD_BOT_LEGACY_poppler="63|poppler|0.47.0-1|*63.dll 65|poppler|0.49.0-2|*65.dll"

# Basic RPM repository layout for this distribution channel.
RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_rpm="\$base/i386/\$arch"
RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_srpm="\$base/i386/SRPMS"
RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_zip="\$base/zip"

# List of repositories for "upload" command (the first one is the default).
RPMBUILD_BOT_UPLOAD_REPO_LIST="exp rel"

# Name of the stable repository (must be present in the above list).
RPMBUILD_BOT_UPLOAD_REPO_STABLE="rel"

# Sanity checks.
check_dir_var "RPM_NETLABS_ORG_DIR"

# Directory in the local filesystem for each repository (usually mapped
# to a WEBAV resource).
RPMBUILD_BOT_UPLOAD_exp_DIR="$RPM_NETLABS_ORG_DIR/experimental/00"
RPMBUILD_BOT_UPLOAD_rel_DIR="$RPM_NETLABS_ORG_DIR/release/00"
