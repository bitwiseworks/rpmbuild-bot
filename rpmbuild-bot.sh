#!/bin/sh

#
# rpmbuild-bot.sh: RPM Build Bot version 1.0.1.
#
# Author: Dmitriy Kuminov <coding@dmik.org>
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#
# History
# -------
#
# 1.0.1 [01.04.2016]
#   - Fix bad variable name error.
#   - Understand .CMD extension in REXX wrapper.
# 1.0 [01.04.2016]
#   - Initial version.
#
# Synopsis
# --------
#
# This script performs a build of RPM packages from a given .spec file in
# controlled environment to guarantee consistent results when building RPMs
# different machines. It uses a separate file rpmbuild-bot-env.sh located
# in the same directory to set up the environment and control the build
# process. The main purpose of this script is to build RPM packages for a
# specific distribution channel maintaining distribution-siecific rules.
#
# Usage
# -----
#
# > rpmbuild-bot.sh SPEC[=VERSION]
# >                 [ upload[=REPO] | test[=MODE] | clean | remove[=REPO] ]
# >                 [-f]
#
# MYAPP is the name of the RPM package spec file (extension is optional,
# .spec is assumed). The spec file is searched in the SPECS directory of the
# rpmbuild tree (usually $USER/rpmbuild/SPECS, rpmbuild --eval='%_specdir'
# will show the exact location). This may be overriden by giving a spec file
# with a path specification.
#
# The second argument defines the command to perform. The default command is
# "build". The following sections will describe each command.
#
# Building packages
# -----------------
#
# The "build" is the main command which is used to generate packages for
# distribution. This command does the following:
#
# 1. Build all RPM packages for all architectures specified in
#    $RPMBUILD_BOT_ARCH_LIST. The packages are stored in the RPMS
#    directory of the rpmbuild tree.
# 2. Build the source RPM. Stored in the SRPMS directory.
# 3. Create a ZIP package from the RPMs for the architecture specified
#    last in $RPMBUILD_BOT_ARCH_LIST.
#
# The build process for each architecture is stored in a log file in the logs
# directory of the rpmbuild tree (`rpmbuild --eval='%_topdir'/logs`). Creation
# of the source RPM and ZIP files is also logged, into separate log files.
#
# The "build" command will fail if the log directory contains files from a
# successfull run of another "build" command for this package. This is to
# prevent overwriting successfully built packages that are not yet uploaded to
# the distribution repository (see the "upload" command). You should either
# run the "upload" command or use the -f option to force removal of these
# log files and the corresponding package files if you are absolutely sure they
# should be discarded.
#
# Doing test builds
# -----------------
#
# The "test" command is used to build packages for one architecture for testing
# purposes. In this more, neither the source RPM nor the ZIP file is created.
# Also, a special option is automatically passed to rpmbuild to clear the %dist
# variable to indicate that this is a local, non-distribution build. Such
# packages will always be "older" than the corresponding builds with %dist
# so that they will be automatically updated on the next yum update to the
# %dist ones. The packages built in "test" mode are NOT intended for
# distribution, they are meant only for local testing before performing the
# full build of everything.
#
# It is possible to configure which steps of the build to perform using the MODE
# argument to the "test" command which may take one of the following values:
#
#   prep    Execute the %prep section of the spec file only.
#   build   Execute the %build section only (requres %prep to be executed
#           before). May be run multiple times.
#   install Execute the %install sectuion only (requires "prep" and "build"
#           to be executed before). May be run multiple times.
#   pack    Create the RPM packages (requires "prep", "build" and "install"
#           to be executed before). Note that this step will also execute
#           the %clean section so that a new "build" + "install" execution is
#           necessary for "pack" to succeed.
#
# When no MODE argument is given, all steps are executed in a proper order.
#
# The results of the "test" command are stored in a log file in the logs/test
# directory of the rpmbuild tree. The previous log file, if any, is given a .bak
# extension (the previous .bak file will be deleted).
#
# Uploading packages
# ------------------
#
# The "upload" command will upload the packages built with the "build"
# command to the official distribution channel configured in
# rpmbuild-bot-env.sh. The REPO argument specifies one of the configured
# repositories. When REPO is not given, the default repository is used
# (usually experimental or similar).
#
# Note that the "upload" command needs log files from the "build" command
# and will fail otherwise.
#
# Upon successful completion, the "upload" command will remove all uploaded
# RPM and ZIP packages and will move all "build" log files to logs/archive.
#
# Cleaning packages
# -----------------
#
# The "clean" command will delete packages built with the "build" command
# and their log files without uploading them to a repository. This is useful
# when the successful build needs to be canceled for some reason (wrong source
# tree state, wrong patches etc.). Note that normally you don't need to use
# this command; it's an emergency-only tool.
#
# Removing packages
# -----------------
#
# The "remove" command allows to remove a particular version of the packages
# built with the "build" command and uploaded with the "upload" command from a
# repository. This is useful when the successful build needs to be canceled for
# some reason (wrong source tree state, wrong patches etc.). Note that normally
# you don't need to use this command; it's an emergency-only tool.
#
# The "remove" command needs log files from the "build" and "upload" commands
# and will fail otherwise. It also requires the VERSION argument for the SPEC
# parameter to be given (to specify the version of the packages to remove) and
# accepts the REPO argument just like the "upload" command does (to specify a
# repository to remove the packages from).
#
# Note that the log files from the "build" and "upload" commands are also removed
# by this command upon sucessful package removal.
#
# Return value
# ------------
#
# The rpmbuild-bot.sh script will return a zero exit code upon successful
# completion and non-zero otherwise. The log files should be inspected to
# check for a reason of the failure.
#

#
# Helpers.
#

run()
{
  "$@"
  local rc=$?
  if test $rc != 0 ; then
    echo "ERROR: The following command failed with error code $rc:"
    echo $@
    exit $rc
  fi
}

log_run()
{
  log="$1"
  shift
  "$@" >"$log" 2>&1
  local rc=$?
  if test $rc != 0 ; then
    echo "ERROR: The following command failed with error code $rc:"
    echo $@
    echo "You will find more information in file '$log'."
    echo "Here are the last 10 lines of output:"
    echo ""
    tail "$log" -n 10
    exit $rc
  fi
}

warn()
{
  echo "WARNING: $1"
}

die()
{
  echo "ERROR: $1"
  exit 1
}

check_dir_var()
{
  eval local val="\$$1"
  [ -n "$val" ] || die "$1 is empty."
  [ -d "$val" ] || die "$1 is '$val', not a valid directory."
}

read_file_list()
{
  # $1 = file list list filename
  # $2 = var name where to save read file names (optional)
  # $3 = function name to call for each file (optional)

  local list="$1"
  local _read_file_list_ret=

  # Check timestamps.
  while read l; do
    local file="${l#*|}"
    local ts="${l%%|*}"
    [ "$file" = "$ts" ] && die "Line '$l' in '$list' does not contain timestamps."
    [ -n "$3" ] && eval $3
    [ -f "$file" ] || die "File '$file' is not found."
    echo "Checking tmestamp of $file..."
    local act_ts=`stat -c '%Y' "$file"`
    if [ "$ts" != "$act_ts" ] ; then
      die "Recorded timestamp $ts doesn't match actual timestamp $act_ts for '$file'."
    fi
    _read_file_list_ret="$_read_file_list_ret${_read_file_list_ret:+ }$file"
  done < "$list"
  # Return the files (if requested).
  [ -n "$2" ] && eval "$2=\$_read_file_list_ret"
}

usage()
{
    echo "Usage:"
    sed -n -e "s/rpmbuild-bot.sh/${0##*/}/g" -e 's/^# > /  /p' < "$0"
    exit 255
}

build_cmd()
{
  # Check settings.
  test -n "$RPMBUILD_BOT_ARCH_LIST" || die "RPMBUILD_BOT_ARCH_LIST is empty."

  local base_arch="${RPMBUILD_BOT_ARCH_LIST##* }"

  echo "Spec file: $spec_full"
  echo "Targets:   $RPMBUILD_BOT_ARCH_LIST + SRPM + ZIP ($base_arch)"

  if [ -f "$spec_list" ] ; then
    if [ -z "$force" ] ; then
      die "File '$spec_list' already exists.
This file indicates a successful build that was not yet uploaded.
Either run the '"'upload'"' command to upload the generated RPM and ZIP
packages to the distribution repository or use the -f option to
force their removal if you are sure they should be discarded."
    fi

    echo "Detected successful build in $spec_list, cleaning up due to -f option..."
    local files=
    read_file_list "$spec_list" files
    for f in $files; do
      echo "Removing $f..."
      run rm -f "$f"
    done
    unset files

    echo "Removing $log_base.*.log and .list files..."
    rm -f "$log_base".*.log "$log_base".*.list "$log_base".list
  fi

  # Generate RPMs.
  for arch in $RPMBUILD_BOT_ARCH_LIST ; do
    echo "Creating RPMs for '$arch' target (logging to $log_base.$arch.log)..."
    log_run "$log_base.$arch.log" rpmbuild.exe --target=$arch -bb "$spec_full"
  done

  # Generate SRPM.
  echo "Creating SRPM (logging to $log_base.srpm.log)..."
  log_run "$log_base.srpm.log" rpmbuild -bs "$spec_full"

  # Find SRPM file name in the log.
  local src_rpm=`grep "^Wrote: \+.*\.src\.rpm$" "$log_base.srpm.log" | sed -e "s#^Wrote: \+##g"`
  [ -n "$src_rpm" ] || die "Cannot find .src.rpm file name in '$log_base.srpm.log'."

  # Find package version.
  local ver_full="${src_rpm%.src.rpm}"
  ver_full="${ver_full##*/}"
  [ "${ver_full%%-[0-9]*}" = "$spec_name" ] || die \
"SRPM name '${src_rpm##*/}' does not match .spec name ('$spec_name').
Either rename '$spec_name.spec' to '${ver_full%%-[0-9]*}.spec' or set 'Name:' tag to '$spec_name'."
  ver_full="${ver_full#${spec_name}-}"
  [ -n "$ver_full" ] || die "Cannot deduce package version from '$src_rpm'."

  # Find all RPM packages for the base arch (note the quotes around `` - it's to preserve multi-line result).
  local rpms="`grep "^Wrote: \+.*\.\($base_arch\.rpm\|noarch\.rpm\)$" "$log_base.$base_arch.log" | sed -e "s#^Wrote: \+##g"`"
  [ -n "$rpms" ] || die "Cannot find .$base_arch.rpm/.noarch.rpm file names in '$log_base.base_arch.log'."

  local ver_full_zip=`echo $ver_full | tr . _`
  local zip="$zip_dir/$spec_name-$ver_full_zip.zip"

  # Generate ZIP.
  echo "Creating ZIP (logging to $log_base.zip.log)..."
  create_zip()
  {(
    run cd "$zip_dir"
    rm -r "@unixroot" 2> /dev/null
    # Note no quoters around $rpms - it's to split at EOL.
    for f in $rpms ; do
      echo "Unpacking $f..."
      run rpm2cpio "$f" | cpio -idm
    done
    rm -f "$zip" 2> /dev/null
    echo "Creating '$zip'..."
    run zip -mry9 "$zip" "@unixroot"
  )}
  log_run "$log_base.zip.log" create_zip

  local ver_list="$log_base.$ver_full.list"

  # Generate list of all generated packages for further reference.
  echo "Creating list file ($ver_list)..."
  echo `stat -c '%Y' "$src_rpm"`"|$src_rpm" > "$ver_list"
  echo `stat -c '%Y' "$zip"`"|$zip" >> "$ver_list"
  # Save base arch RPMs.
  for f in $rpms ; do
    echo `stat -c '%Y' "$f"`"|$f" >> "$ver_list"
  done
  # Save other arch RPMs.
  for arch in ${RPMBUILD_BOT_ARCH_LIST%${base_arch}} ; do
    rpms="`grep "^Wrote: \+.*\.$arch\.rpm$" "$log_base.$arch.log" | sed -e "s#^Wrote: \+##g"`"
    [ -n "$rpms" ] || die "Cannot find .$arch.rpm file names in '$log_base.arch.log'."
    for f in $rpms ; do
      echo `stat -c '%Y' "$f"`"|$f" >> "$ver_list"
    done
  done

  # Everything succeeded. Symlink the list file so that "upload" can find it.
  run ln -s "${ver_list##*/}" "$log_base.list"
}

test_cmd()
{
  echo "Spec file: $spec_full"

  local base_arch="${RPMBUILD_BOT_ARCH_LIST##* }"
  local cmds=

  [ -z "$command_arg" ] && command_arg="all"

  case "$command_arg" in
    all)
      cmds="$cmds -bb"
      ;;
    prep)
      cmds="$cmds -bp --short-circuit"
      ;;
    build)
      cmds="$cmds -bc --short-circuit"
      ;;
    install)
      cmds="$cmds -bi --short-circuit"
      ;;
    pack)
      cmds="$cmds -bb --short-circuit"
      ;;
    *)
      die "Invalid test build sub-command '$command_arg'."
      ;;
  esac

  local log_file="$log_dir/test/$spec_name.log"

  if [ -f "$log_file" ] ; then
    rm -f "$log_file.bak"
    run mv "$log_file" "$log_file.bak"
  fi

  echo "Doing test RPM build for '$base_arch' target (logging to $log_file)..."
  log_run "$log_file" rpmbuild.exe "--define=dist %nil" --target=$base_arch $cmds $spec_full

  # Show the generated RPMs when appropriate.
  if [ "$command_arg" = "all" -o "$command_arg" = "pack" ] ; then
    local rpms=`grep "^Wrote: \+.*\.\($base_arch\.rpm\|noarch\.rpm\)$" "$log_file" | sed -e "s#^Wrote: \+##g"`
    if [ -n "$rpms" ] ; then
      echo "Successfully generated the following RPMs:"
      for f in $rpms ; do
        echo "$f"
      done
    else
      warn "Cannot find .$base_arch.rpm/.noarch.rpm file names in '$log_file'."
    fi
  fi
}

repo_dir_for_file()
{
  # $1 = input file name
  # $2 = var name to save dir to

  [ -n "$1" -a -n "$2" ] || die "Invalid arguments."

  local _repo_dir_for_file_ret=
  case "$1" in
    *.src.rpm)
      eval _repo_dir_for_file_ret="$RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_srpm"
      ;;
    *.*.rpm)
      local arch="${1%.rpm}"
      arch="${arch##*.}"
      [ -n "$arch" ] || die "No arch spec in file name '$1'."
      eval _repo_dir_for_file_ret="$RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_rpm"
      ;;
    *.zip)
      eval _repo_dir_for_file_ret="$RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_zip"
      ;;
  esac

  eval "$2=\$_repo_dir_for_file_ret"
}

upload_cmd()
{
  # Check settings.
  test -n "$RPMBUILD_BOT_UPLOAD_REPO_LIST" || die "RPMBUILD_BOT_UPLOAD_REPO_LIST is empty."

  local repo="$command_arg"
  [ -z "$repo" ] && repo="${RPMBUILD_BOT_UPLOAD_REPO_LIST%% *}"

  check_dir_var "RPMBUILD_BOT_UPLOAD_${repo}_DIR"

  eval local base="\$RPMBUILD_BOT_UPLOAD_${repo}_DIR"

  [ -f "$spec_list" ] || die \
"File '$spec_list' is not found.
You may need to build the packages using the 'build' command."

  local files=
  read_file_list "$spec_list" files
  for f in $files; do
    local d=
    repo_dir_for_file "$f" d
    [ -n "$d" ] || die "Unsupported file name '$f' in '$spec_list'."
    [ -d "$d" ] || die "'$d' is not a directory."
    [ -f "$d/${f##*/}" -a -z "$force" ] && die \
"File '$d/${f##*/}' already exists.
Use the -f option to force uploading if you are sure the existing
packages in the repository should be discarded."
    echo "Copying $f to $d..."
    run cp -p "$f" "$d"
  done

  # On success, delete the uploaded packages and archive log files.
  for f in $files; do
    echo "Removing $f..."
    run rm -f "$f"
  done

  # Note: versioned .list file will remain in archive forever (for further reference).
  echo "Removing old '$spec_name' logs from $log_dir/archive..."
  rm -f "$log_dir/archive/$spec_name".*.log "$log_dir/archive/$spec_name".list
  echo "Moving '$spec_name' logs to $log_dir/archive..."
  run mv "$log_base".*.log "$log_base".*.list "$log_base".list "$log_dir/archive/"
}

clean_cmd()
{
  [ -f "$spec_list" ] || die \
"File '$spec_list' is not found.
You man need to build the packages using the 'build' command."

  local files=
  read_file_list "$spec_list" files

  for f in $files; do
    echo "Removing $f..."
    run rm -f "$f"
  done

  echo "Removing '$spec_name' logs from $log_dir..."
  rm -f "$log_base".*.log "$log_base".*.list "$log_base".list
}

remove_cmd()
{
  # Check settings.
  [ -n "$spec_ver" ] || die "SPEC parameter lacks version specification."

  test -n "$RPMBUILD_BOT_UPLOAD_REPO_LIST" || die "RPMBUILD_BOT_UPLOAD_REPO_LIST is empty."

  local repo="$command_arg"
  [ -z "$repo" ] && repo="${RPMBUILD_BOT_UPLOAD_REPO_LIST%% *}"

  check_dir_var "RPMBUILD_BOT_UPLOAD_${repo}_DIR"

  eval local base="\$RPMBUILD_BOT_UPLOAD_${repo}_DIR"

  local ver_list="$log_dir/archive/$spec_name.$spec_ver.list"
  [ -f "$ver_list" ] || die "File '$ver_list' is not found."

  local files=
  read_file_list "$ver_list" files 'local dir=; repo_dir_for_file $file dir; file="${dir}/${file##*/}"'

  for f in $files; do
    echo "Removing $f..."
    run rm -f "$f"
  done

  echo "Removing $ver_list..."
  run rm -f "$ver_list"

  # Also remove the logs of last "build" if we are removing the last "build" package.
  if [ -L "$log_dir/archive/$spec_name.list" -a \
       `readlink "$log_dir/archive/$spec_name.list"` = "$spec_name.$spec_ver.list" ] ; then
    echo "Removing '$spec_name' logs from $log_dir/archive..."
    rm -f "$log_dir/archive/$spec_name".*.log "$log_dir/archive/$spec_name".list
  fi
}

#
# Main.
#

# Parse command line.
while [ -n "$1" ] ; do
  case "$1" in
  -*)
    options="$*"
    while [ -n "$1" ] ; do
      case "$1" in
      -f) force="-f"
        ;;
      *) usage
        ;;
      esac
      shift
    done
    break
    ;;
  *)
    if [ -z "$spec" ] ; then
      spec="$1"
    else
      command="$1"
    fi
    ;;
  esac
  shift
done

[ -n "$spec" ] || usage
[ -z "$command" ] && command="build"

spec_ver="${spec#*=}"
spec="${spec%=*}"
[ "$spec" = "$spec_ver" ] && spec_ver=

command_name="${command%=*}"
command_arg="${command#*=}"
[ "$command_name" = "$command_arg" ] && command_arg=

need_spec_file=

# Validate commands.
case "$command_name" in
  build|test)
    need_spec_file=1
    ;;
  upload|clean|remove)
    ;;
  *) usage
    ;;
esac

# Query all rpmbuild macros in a single run as it may be slow.
eval `rpmbuild.exe --eval='rpmbuild_dir="%_topdir" ; spec_dir="%_specdir"' | tr '\\\' /`

log_dir="$rpmbuild_dir/logs"
zip_dir="$rpmbuild_dir/zip"

spec=`echo $spec | tr '\\\' /`

spec_name="${spec##*/}"

if [ "$spec_name" = "$spec" ] ; then
  # No path information, use SPECS
  spec_full="$spec_dir/${spec_name%.spec}.spec"
else
  # Has some path, use it.
  spec_full="${spec%.spec}.spec"
fi

spec_name="${spec_name%.spec}"

[ -z "$need_spec_file" -o -f "$spec_full" ] || die "Spec file '$spec_full' is not found."

# Prepare some (non-rpmbuild-standard) directories.
run mkdir -p "$log_dir"
run mkdir -p "$log_dir/archive"
run mkdir -p "$log_dir/test"
run mkdir -p "$zip_dir"

[ -z "$command" ] && command="build"

command_name="${command%=*}"
comand_arg="${command#*=}"
[ "$command_name" = "$command_arg" ] && command_arg=

log_base="$log_dir/$spec_name"
spec_list="$log_base.list"

echo "Package:   $spec_name"
echo "Command:   $command $options"

# Set up the rpmbuild-bot environment.
. "${0%%.sh}-env.sh"

run eval "${command_name}_cmd"

echo "All done."

exit 0
