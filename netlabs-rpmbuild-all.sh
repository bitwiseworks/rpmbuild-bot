#!/bin/sh

# This script takes a .spec file and performs a full rebuild of RPM packages
# for all acrhitectures supported by netlabs.org RPM repositories using the
# environment from the netlabs-rpmbuild-env.sh script (see that file for more
# information about the environment). It also generates a .zip package from
# the last listed architecture (usualliy i386).
#
# Usage:
#
#   sh -c 'netlabs-rpmbuild.all.sh myapp.spec [VAR=VAL]'
#
# where 'myapp.spec' is the name of the spec file and VAR=VAL may be used to
# override values of variables defined in netlabs-rpmbuild-env.sh or to define
# new variables for configure etc. Variables starting from `NETLABS` are used to
# set up the behavior of this script and are not exported to the environment;
# all other variables are expoted (so that they can bee seen in child shells).
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
    echo "You will find more information in file '$log'"
    exit $rc
  fi
}

die()
{
    echo "ERROR: $1"
    exit 1
}

spec=`echo $1 | tr '\\\' /`
test -n "$spec" || { echo "ERROR: You must specify a .spec file."; exit 1; }

spec_name="${spec##*/}"

# Set up official netlabs.org rpmbuild environment
. netlabs-rpmbuild-env.sh

# Override variables from the command line, if any
shift
while test "$1" != "" ; do
    case "$1" in
    NETLABS*=*)
        eval "$1"
    ;;
    *=*)
        eval "export $1"
    ;;
    esac
    shift
done

test -n "$NETLABS_RPM_ARCH_LIST" || die "NETLABS_RPM_ARCH_LIST is empty."

zip_arch=${NETLABS_RPM_ARCH_LIST##* }

echo "Will rpm-build packages from '$spec' for:"
echo "  $NETLABS_RPM_ARCH_LIST + SRPM + ZIP ($zip_arch)"

# Generate RPMs
for arch in $NETLABS_RPM_ARCH_LIST ; do
  echo "Creating RPMs for '$arch' target (logging to $spec_name.$arch.log)..."
  log_run "$spec_name.$arch.log" rpmbuild --target=$arch -bb "$spec"
done

# Generate SRPM
echo "Creating SRPM (logging to $spec_name.srpm.log)..."
log_run "$spec_name.srpm.log" rpmbuild -bs "$spec"

# Generate ZIP
echo "Creating ZIP (logging to $spec_name.zip.log)..."
create_zip()
{(
  zip=`grep "src.rpm" "$spec_name.srpm.log" | sed -e "s#^[a-zA-Z ]*: *##g" -e "s#\.src\.rpm##g" | tr . _`.zip
  zip_dir="${zip%/*}/../zip"
  zip="$zip_dir/${zip##*/}"
  echo "Will create '$zip'"
  run mkdir -p "$zip_dir"
  rm -r "@unixroot" 2> /dev/null
  for rpm in `grep "$zip_arch\.rpm\|noarch\.rpm" "$spec_name.$zip_arch.log" | sed "s#^[a-zA-Z ]*: *##g"` ; do
    echo "Unpacking $rpm..."
    run rpm2cpio "$rpm" | cpio -idm
  done
  rm -f "$zip" 2> /dev/null
  run zip -mry9 "$zip" "@unixroot"
)}
log_run "$spec_name.zip.log" create_zip

echo "All done."
