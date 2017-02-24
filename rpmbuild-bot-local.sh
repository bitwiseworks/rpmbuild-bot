#!/bin/sh

#
# rpmbuild-bot-local.sh: RPM Build Bot site-specific configuration example.
#
# Author: Dmitriy Kuminov <coding@dmik.org>
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#
# Synopsis
# --------
#
# This script sets up the site-specific rpmbuild-bot configuration necessary
# to build RPM packages with rpmbuild-bot.sh.
#
# Copy it to your $HOME directory and alter accordng to the build site.
#

# Local directory to look for .spec files w/o path.
# This is normall an SVN repository where .spec files will be committed
# by the upload command.
RPMBUILD_BOT_SPEC_DIR="D:/Coding/rpm/spec/SPECS"

# Local netdrive directory that maps to rpm.netlabs.org over WEBDAV.
RPM_NETLABS_ORG_DIR="Y:/webdav/rpm.netlabs.org"
