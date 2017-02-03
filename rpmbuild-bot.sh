#!/bin/sh

#
# rpmbuild-bot.sh: RPM Build Bot version 1.1.0.
#
# Author: Dmitriy Kuminov <coding@dmik.org>
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#
# Synopsis
# --------
#
# This script performs a build of RPM packages from a given .spec file in
# controlled environment to guarantee consistent results when building RPMs
# different machines. It uses a separate file rpmbuild-bot-env.sh located
# in the same directory to set up the environment and control the build
# process. The main purpose of this script is to build RPM packages for a
# specific distribution channel maintaining distribution-specific rules.
#
# Usage
# -----
#
# > rpmbuild-bot.sh SPEC[=VERSION]
# >                 [ upload[=REPO] | test[=MODE] | clean[=test] |
# >                   move[=FROM_REPO=TO_REPO] | remove[=REPO] ]
# >                 [-f]
#
# MYAPP is the name of the RPM package spec file (extension is optional,
# .spec is assumed). The spec file is searched in the SPECS directory of the
# rpmbuild tree (usually $USER/rpmbuild/SPECS, rpmbuild --eval='%_specdir'
# will show the exact location). This may be overridden by giving a spec file
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
# successful run of another "build" command for this package. This is to
# prevent overwriting successfully built packages that are not yet uploaded to
# the distribution repository (see the "upload" command). You should either
# run the "upload" command or use the -f option to force removal of these
# log files and the corresponding package files if you are absolutely sure they
# should be discarded.
#
# The "build" command will also check if there is a directory named SPEC in the
# same directory where the given .spec file resides. If such a directory
# exists, all files in it are assumed to be auxiliary source files used by the
# .spec file via SourceN: directives. These files will be automatically copied
# to the SOURCES directory in the rpmbuild tree before starting the build
# process.
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
# parameter to the "test" command which may take one of the following values:
#
#   prep    Execute the %prep section of the spec file only.
#   build   Execute the %build section only (requires %prep to be executed
#           before). May be run multiple times.
#   install Execute the %install section only (requires "prep" and "build"
#           to be executed before). May be run multiple times.
#   pack    Create the RPM packages (requires "prep", "build" and "install"
#           to be executed before). Note that this step will also execute
#           the %clean section so that a new "install" execution is
#           necessary for "pack" to succeed.
#
# When no MODE parameter is given, all steps are executed in a proper order.
#
# The results of the "test" command are stored in a log file in the logs/test
# directory of the rpmbuild tree. The previous log file, if any, is given a .bak
# extension (the previous .bak file will be deleted).
#
# The "test" command will copy auxiliary source files for the .spec file, if any,
# to the proper location before rpmbuild execution -- the same way the "build"
# command does it.
#
# Uploading packages
# ------------------
#
# The "upload" command will upload the packages built with the "build"
# command to the official distribution channel configured in
# rpmbuild-bot-env.sh. The REPO parameter specifies one of the configured
# repositories. When REPO is not given, the default repository is used
# (usually experimental or similar).
#
# The upload command also requires the spec file to be under SVN version control
# and will try to commit it after uploading the RPMs to the repository with the
# automatic commit message that says "spec: PROJECT: Release version VERSION."
# (where PROJECT is the spec file name and VERSION is the full version,
# excluding the distribution mark, as specified by the spec file).This to ensure
# that the spec file is published at the same time the RPMs are published - to
# guarantee their consistency and simplify further maintenance. Note that the
# auxiliary source directory named SPEC and located near the spec file (see the
# "build" command), if it exists, will also be committed. If the spec file is
# not under version control, the "upload" command will fail.
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
# The "clean" command needs log files from the "build" command and will fail
# otherwise.
#
# If the "clean" command is given a "test" parameter, it will clean up the
# results of the "test" command instead of "build". The log file from the
# "test" command needs to be present or the command will fail.
#
# Moving packages between repositories
# ------------------------------------
#
# The "move" command allows to move a particular version of the packages
# built with the "build" command and uploaded with the "upload" command from one
# repository to another one. The "move" command is normally used to move
# packages from a test repository to a production one when they are ready for
# wide distribution.
#
# The "move" command needs log files from the "build" and "upload" commands
# and will fail otherwise. It also requires the VERSION parameter for the SPEC
# argument to be given (to specify the version of the packages to remove) and
# requires the FROM_REPO=TO_REPO parameter itself to specify the source
# repository and the target repository, respectively.
#
# The log files from the "build" and "upload" commands are not removed by the
# "move" command so it may be performed multiple times. The current location
# of the packages is not tracked in the log files so the command will fail
# if the source repository doesn't have the package files or if the target
# repository already has them.
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
# and will fail otherwise. It also requires the VERSION parameter for the SPEC
# argument to be given (to specify the version of the packages to remove) and
# accepts the REPO parameter itself just like the "upload" command does (to
# specify a repository to remove the packages from).
#
# Note that the log files from the "build" and "upload" commands are also
# removed by this command upon successful package removal.
#
# Return value
# ------------
#
# The rpmbuild-bot.sh script will return a zero exit code upon successful
# completion and non-zero otherwise. The script output and log files should be
# inspected to check for a reason of the failure.
#

#
# Helpers.
#

print_elapsed()
{
  # $1 = start timestamp, in seconds (as returned by `date +%s`)
  # $2 = string containg \$e (will be replaced with the elapsed time)

  [ -z "$1" -o -z "$2" ] && return

  local e=$(($(date +%s) - $1))
  local e_min=$(($e / 60))
  local e_sec=$(($e % 60))
  local e_hrs=$((e_min / 60))
  e_min=$((e_min % 60))
  e="${e_hrs}h ${e_min}m ${e_sec}s"

  eval "echo \"$2\""
}

quit()
{
  if [ -n "$start_time" ] ; then
    echo "Build ended on $(date -R)."
    if [ $1 = 0 ] ; then
      print_elapsed start_time "Build succeeded (took \$e)."
    else
      print_elapsed start_time "Build failed (took \$e)."
    fi
  fi
  exit $1
}

run()
{
  "$@"
  local rc=$?
  if test $rc != 0 ; then
    echo "ERROR: The following command failed with error code $rc:"
    echo $@
    quit $rc
  fi
}

log_run()
{
  log="$1"
  shift
  rm -f "$log"
  "$@" >"$log" 2>&1
  local rc=$?
  if test $rc != 0 ; then
    echo "ERROR: The following command failed with error code $rc:"
    echo $@
    echo "You will find more information in file '$log'."
    echo "Here are the last 10 lines of output:"
    echo "------------------------------------------------------------------------------"
    tail "$log" -n 10
    echo "------------------------------------------------------------------------------"
    quit $rc
  fi
}

warn()
{
  echo "WARNING: $1"
}

die()
{
  echo "ERROR: $1"
  quit 1
}

check_dir_var()
{
  eval local val="\$$1"
  [ -n "$val" ] || die "$1 is empty."
  [ -d "$val" ] || die "$1 is '$val', not a valid directory."
}

read_file_list()
{
  # $1 = file list filename
  # $2 = var name where to save the list of read file names (optional)
  # $3 = function name to call for each file (optional), it may assign a new
  #      file name to $file and also set $file_pre and $file_post that will
  #      be prepended and appended to $file when saving it to the list in $2
  #      (but not when checking for file existence and timestamp)

  local list="$1"
  local _read_file_list_ret=

  # Check timestamps.
  while read l; do
    local file_pre=
    local file_post=
    local file="${l#*|}"
    local ts="${l%%|*}"
    [ "$file" = "$ts" ] && die "Line '$l' in '$list' does not contain timestamps."
    [ -n "$3" ] && eval $3
    [ -f "$file" ] || die "File '$file' is not found."
    echo "Checking timestamp of $file..."
    local act_ts=`stat -c '%Y' "$file"`
    # Drop fractional part of seconds reported by older coreutils
    ts="${ts%%.*}"
    act_ts="${act_ts%%.*}"
    if [ "$ts" != "$act_ts" ] ; then
      die "Recorded timestamp $ts doesn't match actual timestamp $act_ts for '$file'."
    fi
    _read_file_list_ret="$_read_file_list_ret${_read_file_list_ret:+ }$file_pre$file$file_post"
  done < "$list"
  # Return the files (if requested).
  [ -n "$2" ] && eval "$2=\$_read_file_list_ret"
}

usage()
{
  echo "Usage:"
  sed -n -e "s/rpmbuild-bot.sh/${0##*/}/g" -e 's/^# > /  /p' < "$0"
  quit 255
}

sync_aux_src()
{
  [ -n "$src_dir" ] || die "src_dir is empty."
  [ -n "$spec_full" ] || die "spec_full is empty."

  # Aux source dir is expected along the .spec file.
  local aux_src_dir="${spec_full%.spec}"

  # Return early if there is no aux source dir.
  [ -d "$aux_src_dir" ] || return

  echo "Copying auxiliary sources for '$spec' to $src_dir..."

  for f in "$aux_src_dir"/* ; do
    local ts=`stat -c '%Y' "$f"`
    local trg_ts=
    local trg_f="$src_dir/${f##*/}"
    [ -f "$trg_f" ] && trg_ts=`stat -c '%Y' "$trg_f"`
    if [ "$ts" != "$trg_ts" ] ; then
      echo "Copying $f..."
      run cp -p "$f" "$trg_f"
    fi
  done
}

get_legacy_runtime()
{
  [ -n "$src_dir" ] || die "src_dir is empty."
  [ -n "$RPMBUILD_BOT_UPLOAD_REPO_STABLE" ] || die "RPMBUILD_BOT_UPLOAD_REPO_STABLE is empty."

  local spec_name_=`echo "${spec_name}" | tr - _`
  eval local arch_list="\${RPMBUILD_BOT_ARCH_LIST_${spec_name_}}"
  [ -z "$arch_list" ] && arch_list="${RPMBUILD_BOT_ARCH_LIST}"

  eval local rpm_list="\${RPMBUILD_BOT_LEGACY_${spec_name_}}"
  [ -z "$rpm_list" ] && return # nothing to do

  eval local base="\$RPMBUILD_BOT_UPLOAD_${RPMBUILD_BOT_UPLOAD_REPO_STABLE}_DIR"

  local abi_list=

  for rpm_spec in "$rpm_list" ; do
    local abi name ver mask legacy_arch
    IFS='|' read abi name ver mask legacy_arch <<EOF
${rpm_spec}
EOF
    [ -z "$abi" -o -z "$name" -o -z "$ver" ] && die "Value '${rpm_spec}' in RPMBUILD_BOT_LEGACY_${spec_name_} is invalid."
    [ -z "$mask" ] && mask="*.dll"

    abi_list="$abi_list${abi_list:+ }$abi"

    # Enumerate RPMs for all archs and extract them
    echo "Getting legacy runtime ($mask) for ABI '$abi'..."
    for arch in ${legacy_arch:-${arch_list}} ; do
      eval local rpm="$RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_rpm/$name-$ver$dist_mark.$arch.rpm"
      local tgt_dir="$src_dir/$spec_name-legacy/$abi/$arch"
      # Check filenames and timestamps
      echo "Checking package $rpm..."
      [ -f "$rpm" ] || die "File '$rpm' is not found."
      local old_ts old_rpm old_name old_ver
      [ -f "$tgt_dir.list" ] && IFS='|' read old_ts old_rpm old_name old_ver < "$tgt_dir.list"
      local ts=`stat -c '%Y' "$rpm"`
      # Drop fractional part of seconds reported by older coreutils
      ts="${ts%%.*}"
      old_ts="${old_ts%%.*}"
      if [ "$old_rpm" != "$rpm" -o "$ts" != "$old_ts" -o "$name" != "$old_name" -o "$ver" != "$old_ver" ] ; then
        echo "Extracting to $tgt_dir..."
        run rm -f "$tgt_dir.list" && rm -rf "$tgt_dir"
        (run mkdir -p "$tgt_dir" && cd "$tgt_dir"; run rpm2cpio "$rpm" | eval cpio -idm $mask)
        # save the list for later use
        find "$tgt_dir" -type f -printf '/%P\n' > "$tgt_dir.files.list"
        # now try to locate the debuginfo package and extract *.dbg from it
        eval local debug_rpm="$RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_rpm/$name-debuginfo-$ver$dist_mark.$arch.rpm"
        [ ! -f "$debug_rpm" ] && eval debug_rpm="$RPMBUILD_BOT_UPLOAD_REPO_LAYOUT_rpm/$name-debug-$ver$dist_mark.$arch.rpm"
        if [ -f "$debug_rpm" ] ; then
          echo "Found debug info package $debug_rpm, extracting..."
          local dbgfilelist="$tgt_dir.debugfiles.list"
          run rm -rf "$dbgfilelist"
          local masks=
          local f
          while read -r f ; do
            f="${f%.*}.dbg"
            masks="$masks${masks:+ }'*$f'"
            # Save the file for later inclusion into debugfiles.list (%debug_package magic in brp-strip.os2)
            run echo "$f" >> "$dbgfilelist"
          done < "$tgt_dir.files.list"
          (run cd "$tgt_dir"; run rpm2cpio "$debug_rpm" | eval cpio -idm $masks)
        fi
        # put the 'done' mark
        run echo "$ts|$rpm|$name|$ver" > "$tgt_dir.list"
      fi
    done
  done

  run echo "$abi_list" > "$src_dir/$spec_name-legacy/abi.list"
}

build_prepare()
{
  sync_aux_src
  get_legacy_runtime
}

build_cmd()
{
  local spec_name_=`echo "${spec_name}" | tr - _`
  eval local arch_list="\${RPMBUILD_BOT_ARCH_LIST_${spec_name_}}"
  [ -z "$arch_list" ] && arch_list="${RPMBUILD_BOT_ARCH_LIST}"

  local base_arch="${arch_list##* }"

  echo "Spec file: $spec_full"
  echo "Targets:   $arch_list + SRPM + ZIP ($base_arch)"

  build_prepare

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

  local noarch_only=
  local start_time=

  # Generate RPMs (note that base_arch always goes first).
  for arch in $base_arch ${arch_list%${base_arch}} ; do
    echo "Creating RPMs for '$arch' target (logging to $log_base.$arch.log)..."
    start_time=$(date +%s)
    log_run "$log_base.$arch.log" rpmbuild.exe --target=$arch -bb "$spec_full"
    print_elapsed start_time "Completed in \$e."
    if ! grep -q "^Wrote: \+.*\.$arch\.rpm$" "$log_base.$arch.log" ; then
      if ! grep -q "^Wrote: \+.*\.noarch\.rpm$" "$log_base.$arch.log" ; then
        die "Target '$arch' did not produce any RPMs."
      fi
      noarch_only=1
      echo "Skipping other targets because '$arch' produced only 'noarch' RPMs."
      break
    fi
  done

  # Generate SRPM.
  echo "Creating SRPM (logging to $log_base.srpm.log)..."
  start_time=$(date +%s)
  log_run "$log_base.srpm.log" rpmbuild -bs "$spec_full"
  print_elapsed start_time "Completed in \$e."

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
  start_time=$(date +%s)
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
  print_elapsed start_time "Completed in \$e."

  local ver_list="$log_base.$ver_full.list"

  # Generate list of all generated packages for further reference.
  echo "Creating list file ($ver_list)..."
  rm -f "$ver_list"
  echo `stat -c '%Y' "$src_rpm"`"|$src_rpm" > "$ver_list"
  echo `stat -c '%Y' "$zip"`"|$zip" >> "$ver_list"
  # Save base arch RPMs.
  for f in $rpms ; do
    echo `stat -c '%Y' "$f"`"|$f" >> "$ver_list"
  done
  # Save other arch RPMs (only if there is anything but noarch).
  if [ -z "$noarch_only" ] ; then
    for arch in ${arch_list%${base_arch}} ; do
      rpms="`grep "^Wrote: \+.*\.$arch\.rpm$" "$log_base.$arch.log" | sed -e "s#^Wrote: \+##g"`"
      [ -n "$rpms" ] || die "Cannot find .$arch.rpm file names in '$log_base.arch.log'."
      for f in $rpms ; do
        echo `stat -c '%Y' "$f"`"|$f" >> "$ver_list"
      done
    done
  fi

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
      build_prepare
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
    # Find all RPM packages for the base arch (note the quotes around `` - it's to preserve multi-line result).
    local rpms="`grep "^Wrote: \+.*\.\($base_arch\.rpm\|noarch\.rpm\)$" "$log_file" | sed -e "s#^Wrote: \+##g"`"
    if [ -n "$rpms" ] ; then
      echo "Successfully generated the following RPMs:"
      for f in $rpms; do
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

  # Prepare for committing the SPEC and auxiliary source files.
  local ver_list=
  [ -L "$spec_list" ] && ver_list=`readlink "$spec_list"`
  [ -z "$ver_list" ] && die "File '$spec_list' is not a valid symbolic link."

  local ver_full="${ver_list#${spec_name}.}"
  ver_full="${ver_full%.*}"
  [ -n "$dist_mark" ] && ver_full="${ver_full%${dist_mark}}"

  [ -n "$ver_full" ] || die "Full version string is empty."

  local commit_items="$spec_full"
  # Commit the auxiliary source directory, if any, as well.
  [ -d "${spec_full%.spec}" ] && commit_items="$commit_items ${spec_full%.spec}"

  local commit_msg="spec: $spec_name: Release version $ver_full."

  echo \
"Uploading requires the following items to be committed to SPEC repository:
  $commit_items
With the following commit message:
  $commit_msg
The repository will be updated now and then you will get a diff for careful
inspection. Type YES to continue."

  local answer=
  read answer
  if [ "$answer" = "YES" ] ; then
    local pager=`which less`
    if [ ! -x "$pager" ] ; then
      pager="more"
      # OS/2 more doesn't understand LF, feed it through sed.
      [ -x `which sed` ] && pager="sed -e '' | $pager"
    fi
    run svn up "$spec_dir"
    echo
    svn diff $commit_items | "$pager"
    echo "
Type YES if the diff is okay to be committed."
    read answer
  fi

  [ "$answer" = "YES" ] || die "Your answer is not YES, upload is aborted."

  # First, copy RPM files over to the repository.
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

  # And finally commit the SPEC file.
  run svn commit $commit_items -m "$commit_msg"

  # Note: versioned .list file will remain in archive forever (for further reference).
  echo "Removing old '$spec_name' logs from $log_dir/archive..."
  rm -f "$log_dir/archive/$spec_name".*.log "$log_dir/archive/$spec_name".list
  echo "Moving '$spec_name' logs to $log_dir/archive..."
  run mv "$log_base".*.log "$log_base".*.list "$log_base".list "$log_dir/archive/"
}

clean_cmd()
{
  if [ "$command_arg" = "test" ] ; then
    # Cleanup after "test" command.
    local base_arch="${RPMBUILD_BOT_ARCH_LIST##* }"
    local log_file="$log_dir/test/$spec_name.log"

    [ -f "$log_file" ] || die "File '$test_log' is not found."

    # Find all RPM packages for the base arch (note the quotes around `` - it's to preserve multi-line result).
    local rpms="`grep "^Wrote: \+.*\.\($base_arch\.rpm\|noarch\.rpm\)$" "$log_file" | sed -e "s#^Wrote: \+##g"`"
    if [ -n "$rpms" ] ; then
      for f in $rpms; do
        echo "Removing $f..."
        run rm -f "$f"
      done
      echo "Removing $log_file[.bak]..."
      run rm -f "$log_file" "$log_file".bak
    else
      die "Cannot find .$base_arch.rpm/.noarch.rpm file names in '$log_file'."
    fi

    return
  fi

  # Cleanup after "build command".
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

move_cmd()
{
  # Check settings.
  [ -n "$spec_ver" ] || die "SPEC parameter lacks version specification."

  test -n "$RPMBUILD_BOT_UPLOAD_REPO_LIST" || die "RPMBUILD_BOT_UPLOAD_REPO_LIST is empty."

  local from_repo="${command_arg%%=*}"
  local to_repo="${command_arg#*=}"

  [ -n "$from_repo" ] || die "FROM_REPO parameter is missing."
  [ "$from_repo" = "$to_repo" ] && die "TO_REPO parameter is missing (or equals to FROM_REPO)."

  check_dir_var "RPMBUILD_BOT_UPLOAD_${from_repo}_DIR"
  check_dir_var "RPMBUILD_BOT_UPLOAD_${to_repo}_DIR"

  local ver_list="$log_dir/archive/$spec_name.$spec_ver.list"
  [ -f "$ver_list" ] || die "File '$ver_list' is not found."

  eval local from_base="\$RPMBUILD_BOT_UPLOAD_${from_repo}_DIR"
  eval local to_base="\$RPMBUILD_BOT_UPLOAD_${to_repo}_DIR"

  local files=
  read_file_list "$ver_list" files '
local dir=
local base="$from_base"; repo_dir_for_file "$file" dir; file="${dir}/${file##*/}"
base="$to_base"; repo_dir_for_file "$file" dir; file_post=">${dir}"
'
  for f in $files; do
    local from="${f%%>*}"
    local to="${f#*>}"
    echo "Moving $from to $to/..."
    run mv "$from" "$to/"
  done
}

remove_cmd()
{
  # Check settings.
  [ -n "$spec_ver" ] || die "SPEC parameter lacks version specification."

  test -n "$RPMBUILD_BOT_UPLOAD_REPO_LIST" || die "RPMBUILD_BOT_UPLOAD_REPO_LIST is empty."

  local repo="$command_arg"
  [ -z "$repo" ] && repo="${RPMBUILD_BOT_UPLOAD_REPO_LIST%% *}"

  check_dir_var "RPMBUILD_BOT_UPLOAD_${repo}_DIR"

  local ver_list="$log_dir/archive/$spec_name.$spec_ver.list"
  [ -f "$ver_list" ] || die "File '$ver_list' is not found."

  eval local base="\$RPMBUILD_BOT_UPLOAD_${repo}_DIR"

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

command_name="${command%%=*}"
command_arg="${command#*=}"
[ "$command_name" = "$command_arg" ] && command_arg=

need_spec_file=

# Validate commands.
case "$command_name" in
  build|test)
    need_spec_file=1
    ;;
  upload|clean|move|remove)
    ;;
  *) usage
    ;;
esac

# Query all rpmbuild macros in a single run as it may be slow.
eval `rpmbuild.exe --eval='rpmbuild_dir="%_topdir" ; spec_dir="%_specdir" ; src_dir="%_sourcedir" ; dist_mark="%dist"' | tr '\\\' /`

[ -n "$rpmbuild_dir" -a -d "$rpmbuild_dir" ] || die "Falied to get %_topdir from rpmbuild or not directory ($rpmbuild_dir)."
[ -n "$spec_dir" -a -d "$spec_dir" ] || die "Falied to get %_specdir from rpmbuild or not directory ($spec_dir)."
[ -n "$src_dir" -a -d "$src_dir" ] || die "Falied to get %_sourcedir from rpmbuild or not directory ($src_dir)."

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

log_base="$log_dir/$spec_name"
spec_list="$log_base.list"

start_time=$(date +%s)

echo "Build started on $(date -R)."
echo "Package:   $spec_name"
echo "Command:   $command $options"

# Set up the rpmbuild-bot environment.
. "${0%%.sh}-env.sh"

# Check common settings.
test -n "$RPMBUILD_BOT_ARCH_LIST" || die "RPMBUILD_BOT_ARCH_LIST is empty."

run eval "${command_name}_cmd"

quit 0
