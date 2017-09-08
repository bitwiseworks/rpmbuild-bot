#!/usr/bin/env python

#
# RPM Build Bot 2
#
# Author: Dmitriy Kuminov <coding@dmik.org>
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#

VERSION = '0.1'


RPM_EXE = 'rpm.exe'
RPMBUILD_EXE = 'rpmbuild.exe'
RPM2CPIO_EXE = 'rpm2cpio.exe'
CPIO_EXE = 'cpio.exe'


SCRIPT_INI_FILE = 'rpmbuild-bot2.ini'
SCRIPT_LOG_FILE = 'rpmbuild-bot2.log'

DATETIME_FMT = '%Y-%m-%d %H:%M:%S'


import sys, os, re, copy, argparse, ConfigParser, subprocess, datetime, traceback, shutil


#
# Overrides ConfigParser to provide improved INI file reading by adding support
# for the Python 3 `${section.option}` interpolation flavor. The idea is taken
# from https://stackoverflow.com/a/35877548. Also adds the following extensions:
#
# - #getlist that interprets the option's value as list of strings separated by
#   the given separator, and #getlines and #getwords shortcuts.
# - Support for `${ENV:<NAME>}` interpolation that is replaced with the contents
#   of the <NAME> environment variable.
# - Support for `${SHELL:<COMMAND>}` interpolation that is replaced with the
#   standard output of <COMMAND> run by the shell.
# - Support for `${RPM:<NAME>}` interpolation that is replaced with the value
#   of the <NAME> RPM macro.
# - Support for copy.deepcopy.
#

class Config (ConfigParser.SafeConfigParser):

  def __init__ (self, rpm_macros, *args, **kwargs):

    self.get_depth = 0
    self.rpm_macros = rpm_macros
    ConfigParser.SafeConfigParser.__init__ (self, *args, **kwargs)

  def __deepcopy__ (self, memo):

    copy = Config (self.rpm_macros, defaults = self.defaults ())
    copy.rpm_macros = self.rpm_macros
    for s in self.sections ():
      copy.add_section (s)
      for (n, v) in self.items (s):
        copy.set (s, n, v)
    return copy

  def get (self, section, option = None, raw = False, vars = None):

    if not option:
      section, option = section.split (':')

    ret = ConfigParser.SafeConfigParser.get (self, section, option, True, vars)
    if raw:
      return ret

    for f_section, f_option in re.findall (r'\$\{(\w+:)?((?<=SHELL:).+|\w+)\}', ret):
      self.get_depth = self.get_depth + 1
      if self.get_depth < ConfigParser.MAX_INTERPOLATION_DEPTH:
        if f_section == 'ENV:':
          sub = os.environ.get (f_option)
          if not sub: raise ConfigParser.NoOptionError, (f_option, f_section [:-1])
        elif f_section == 'SHELL:':
          sub = subprocess.check_output (f_option, shell = True).strip ()
        elif f_section == 'RPM:':
          if f_option not in self.rpm_macros:
            sub = subprocess.check_output ([RPM_EXE, '--eval', '%%{?%s}' % f_option]).strip ()
            self.rpm_macros [f_option] = sub
          else:
            sub = self.rpm_macros [f_option]
        else:
          sub = self.get (f_section [:-1] or section, f_option, vars = vars)
        ret = ret.replace ('${{{0}{1}}}'.format (f_section, f_option), sub)
      else:
        raise ConfigParser.InterpolationDepthError, (option, section, ret)

    self.get_depth = self.get_depth - 1
    return ret

  def getlist (self, section_option, sep):
    return filter (None, self.get (section_option).split (sep))

  def getlines (self, section_option): return self.getlist (section_option, '\n')

  def getwords (self, section_option): return self.getlist (section_option, None)


#
# Generic error exception for this script.
#

class Error (BaseException):
  code = 101
  def __init__ (self, prefix, msg = None):
    self.prefix = prefix if msg else None
    self.msg = msg if msg else prefix
    BaseException.__init__ (self, (self.prefix and self.prefix + ': ' or '') + self.msg)


#
# Error exception for #run and #run_pipe functions.
#

class RunError (Error):
  code = 102
  def __init__ (self, cmd, msg, log_file = None):
    self.cmd = cmd
    self.log_file = log_file
    Error.__init__ (self, msg)

#
# Logs a message to the console and optionally to a file.
#

def log (msg):

  if msg [-1] != '\n':
    msg += '\n'

  if g_output_file:

    g_output_file.write (msg)

    # Note: obey log_to_console only if the console is not redirected to a file.
    if g_args.log_to_console and g_log != sys.stdout:
      sys.stdout.write (msg)

  else:

    sys.stdout.write (msg)

    # Note: log to the file only when the console is not redirected to it.
    if g_log and g_log != sys.stdout:
      g_log.write (msg)


#
# Same as log but prepends ERROR: to the message.
#

def log_err (prefix, msg = None):

  if not msg:
    msg = prefix
    prefix = None

  if not msg [-1] in '.:':
    msg += '.'
  log ('ERROR: ' + (prefix and prefix + ': ' or '') + msg)


#
# Prepare a log file by backing up the previous one.
#

def rotate_log (log_file):

  if os.path.exists (log_file):

    try: os.remove (log_file + '.bak')
    except OSError: pass

    try: os.rename (log_file, log_file + '.bak')
    except OSError as e:
      raise Error ('Cannot rename `%(log_file)s` to `.bak`: %(e)s' % locals ())

#
# Ensures path is a directory and exists.
#

def ensure_dir (path):

  try:
    if not os.path.isdir (path):
      os.makedirs (path)
  except OSError as e:
    raise Error ('Cannot create directory `%(path)s`: %(e)s' % locals ())

#
# Removes a file or a directory (including its contents) at path. Does not raise
# an exception if the path does not exist.
#

def remove_path (path):

  try:
    if os.path.isfile (path):
      os.remove (path)
    elif os.path.isdir (path):
      shutil.rmtree (path)
  except OSError as e:
    if e.errno != 2:
      raise


#
# Executes a pipeline of commands with each command running in its own process.
# If regex is not None, matching lines of the pipeline's output will be returned
# as a list. If file is not None, all output will be sent to the given file
# object using its write method and optionally sent to the console if
# g_args.log_to_console is also set.
#
# Note that commands is expected to be a list where each entry is also a list
# which is passed to subprocess.Popen to execute a command. If there is only
# one command in the list, then it is simply executed in a new process witout
# building up a pipeline.
#
# Raises Error if execution fails or terminates with a non-zero exit code.
#

def run_pipe (commands, regex = None, file = None):

  if not file:
    file = g_output_file

  recomp = re.compile (regex) if regex else None
  lines = []
  rc = 0

  # Note: obey log_to_console only if the console is not redirected to a file.
  # Also makes no sense to capture if file equals to sys.stdout (unless regex
  # is given). If file is None, we have to capture to hide any output at all.
  duplicate_output = g_args.log_to_console and g_log != sys.stdout and file != sys.stdout
  capture_output = duplicate_output or recomp or not file

  try:

    cmd = commands [0]

    if len (commands) == 1:

      if capture_output:
        proc = subprocess.Popen (cmd, stdout = subprocess.PIPE, stderr = subprocess.STDOUT, bufsize = 1)
        capture_file = proc.stdout
      else:
        proc = subprocess.Popen (cmd, stdout = file, stderr = subprocess.STDOUT, bufsize = 1)

    else:

      if capture_output:
        # Note: We can't use proc.stderr here as it's only a read end.
        rpipe, wpipe = os.pipe ()
        capture_file = os.fdopen (rpipe)
      else:
        wpipe = file

      proc = subprocess.Popen (cmd, stdout = subprocess.PIPE, stderr = wpipe)

      last_proc = proc
      for cmd in commands [1:]:
        last_proc = subprocess.Popen (cmd, stdin = last_proc.stdout,
                                      stdout = wpipe if cmd == commands [-1] else subprocess.PIPE,
                                      stderr = wpipe)

      if capture_output:
        os.close (wpipe)

    if capture_output:
      for line in iter (capture_file.readline, ''):
        if recomp:
          lines += recomp.findall (line)
        if duplicate_output:
          sys.stdout.write (line)
        if file:
          file.write (line)

    if len (commands) > 1:
      # TODO: we ignore the child exit code at the moment due to this bug:
      # http://trac.netlabs.org/rpm/ticket/267#ticket
      # Once it's fixed, we should report it to the caller. Note that we can
      # ignore it now only because CPIO_EXE luckily works per se, it just can't
      # close its end of the pipe gracefully (and e.g grep doesnt' work at all).
      rc = last_proc.wait ()

    rc = proc.wait ()
    msg = 'exit code %d' % rc

  except OSError as e:

    rc = 1
    msg = 'error %d (%s)' % (e.errno, e.strerror)

  finally:

    if rc:
      raise RunError (' '.join (cmd), msg)

  return lines


#
# Shortcut to #run_pipe for one command.
#

def run (command, regex = None):
  return run_pipe ([command], regex)


#
# Similar to #run_pipe but all output produced by and external commands will be
# be redirected to a log file.
#

def run_pipe_log (log_file, commands, regex = None):

  with open (log_file, 'w', buffering = 1) as f:

    start_ts = datetime.datetime.now ()
    f.write ('[%s, %s]\n' % (start_ts.strftime (DATETIME_FMT), ' | '.join (' '.join(c) for c in commands)))

    try:

      rc = 0
      msg = 'exit code 0'
      lines = run_pipe (commands, regex, f)

    except RunError as e:

      rc = 1
      cmd = e.cmd
      msg = e.msg

    finally:

      end_ts = datetime.datetime.now ()
      elapsed = str (end_ts - start_ts).rstrip ('0')
      f.write ('[%s, %s, %s s]\n' % (end_ts.strftime (DATETIME_FMT), msg, elapsed))

      if rc:
        raise Error ('The following command failed with %s:\n'
                     '  %s'
                     % (msg, ' '.join (cmd)),
                     log_file = log_file)

  return lines


#
# Shortcut to #run_pipe_log for one command.
#

def run_log (log_file, command, regex = None):
  return run_pipe_log (log_file, [command], regex)


#
# Similar to #run_log but runs a Python function. All output produced by #log
# and external commands run via #run and #run_pipe within this function will be
# redirected to a log file.
#

def func_log (log_file, func):

  with open (log_file, 'w', buffering = 1) as f:

    start_ts = datetime.datetime.now ()
    f.write ('[%s, Python %s]\n' % (start_ts.strftime (DATETIME_FMT), str (func)))

    try:

      # Cause redirection of #log to the given file.
      global g_output_file
      g_output_file = f

      rc = func () or 0
      msg = 'return code %d' % rc

    except RunError as e:

      rc = 1
      f.write ('%s: %s\n' % (e.cmd, e.msg))

    except:

      rc = 1
      f.write ('Unexpected exception occured:\n%s' % traceback.format_exc ())

    finally:

      g_output_file = None

      if rc:
        msg = 'exception ' + sys.exc_type.__name__

      end_ts = datetime.datetime.now ()
      elapsed = str (end_ts - start_ts).rstrip ('0')
      f.write ('[%s, %s, %s s]\n' % (end_ts.strftime (DATETIME_FMT), msg, elapsed))

      if rc:
        raise RunError (str (func), msg, log_file = log_file)


#
# Searches for a spec file in the provided path or in spec_dirs if no path is
# provided in spec. Assumes the `.spec` extension if it is missing. If the spec
# file is found, this function will do the following:
#
# - Load `rpmbuild-bot2.ini` into config if it exists in a spec_dirs directory
#   containing the spec file (directly or through children).
# - Load `rpmbuild-bot2.ini` into config if it exists in the same directory
#   where the spec file is found.
# - Log the name of the found spec file.
# - Return a tuple with the full path to the spec file, spec base name (w/p
#   path or extension) and full path to the auxiliary source directory for this
#   spec.
#
# Otherwise, Error is raised and no INI files are loaded.
#

def resolve_spec (spec, spec_dirs, config):

  found = 0

  if os.path.splitext (spec) [1] != '.spec' :
    spec += '.spec'

  dir = os.path.dirname (spec)
  if dir:
    spec_base = os.path.splitext (os.path.basename (spec)) [0]
    full_spec = os.path.abspath (spec)
    if os.path.isfile (full_spec):
      found = 1
      full_spec_dir = os.path.dirname (full_spec)
      for dirs in spec_dirs:
        for d in dirs:
          if os.path.samefile (d, full_spec_dir) or \
             os.path.samefile (os.path.join (d, spec_base), full_spec_dir):
            found = 2
            break
        else:
          continue
        break
  else:
    spec_base = os.path.splitext (spec) [0]
    for dirs in spec_dirs:
      for d in dirs:
        full_spec = os.path.abspath (os.path.join (d, spec))
        if os.path.isfile (full_spec):
          found = 2
          break
        else:
          full_spec = os.path.abspath (os.path.join (d, spec_base, spec))
          if os.path.isfile (full_spec):
            found = 2
            break
      else:
        continue
      break

  # Load directory INI files
  if found == 2:
    config.read (os.path.join (dirs [0], SCRIPT_INI_FILE))
    if not os.path.samefile (d, dirs [0]):
      config.read (os.path.join (d, SCRIPT_INI_FILE))

  # Load spec INI file
  if found >= 1:
    config.read (os.path.join (os.path.dirname (full_spec), SCRIPT_INI_FILE))

  # Figure out the auxiliary source dir for this spec
  spec_aux_dir = os.path.dirname (full_spec)
  if (os.path.basename (spec_aux_dir) != spec_base):
    spec_aux_dir = os.path.join (spec_aux_dir, spec_base)

  if (found == 0):
    if dir:
      raise Error ('Cannot find `%s`' % spec)
    else:
      raise Error ('Cannot find `%s` in %s' % (spec, spec_dirs))

  log ('Spec file: %s' % full_spec)
  log ('Spec source dir: %s' % spec_aux_dir)

  # Validate some mandatory config options.
  if not config.get ('general:archs'):
    raise Error ('config', 'No value for option `general:archs`');

  return (full_spec, spec_base, spec_aux_dir)


#
# Prepare for build and test commands. This includes the following:
#
# - Download legacy runtime libraries for the given spec if spec legacy is
#   configured.
#
# TODO: Actually implement it.
#

def build_prepare (full_spec, spec_base):

  pass


#
# Build command.
#

def build_cmd ():

  for spec in g_args.SPEC.split (','):

    config = copy.deepcopy (g_config)
    full_spec, spec_base, spec_aux_dir = resolve_spec (spec, g_spec_dirs, config)

    archs = config.getwords ('general:archs')

    log ('Targets: ' + ', '.join (archs) + ', ZIP (%s), SRPM' % archs [0])

    log_base = os.path.join (g_log_dir, 'build', spec_base)
    ensure_dir (log_base)

    # Generate RPMs for all architectures.

    noarch_only = True
    base_rpms = None

    for arch in archs:

      log_file = os.path.join (log_base, '%s.log' % arch)
      log ('Creating RPMs for `%(arch)s` target (logging to %(log_file)s)...' % locals ())

      rpms = run_log (log_file, [RPMBUILD_EXE, '--target=%s' % arch, '-bb',
                                 '--define=_sourcedir %s' % spec_aux_dir, full_spec],
                      r'^Wrote: +(.+\.(?:%s|noarch)\.rpm)$' % arch)

      if len (rpms):
        # Save the base arch RPMs for later.
        if not base_rpms:
          base_rpms = rpms
        # Save the for noarch only.
        for r in rpms:
          if r.endswith ('.%s.rpm' % arch):
            noarch_only = False
            break
        if noarch_only:
          log ('Skipping other targets because `%s` produced only `noarch` RPMs.' % arch)
          break
      else:
        raise Error ('Cannot find `.(%(arch)s|noarch).rpm` file names in `%(log_file)s`.' % locals ())

    # Generate SRPM.

    log_file = os.path.join (log_base, 'srpm.log')
    log ('Creating SRPM (logging to %s)...' % log_file)

    rpms = run_log (log_file, [RPMBUILD_EXE, '-bs',
                               '--define=_sourcedir %s' % spec_aux_dir, full_spec],
                    r'^Wrote: +(.+\.src\.rpm)$')

    if not len (rpms):
      raise Error ('Cannot find `.src.rpm` file name in `%s`.' % log_file)

    # Find package version.

    srpm = os.path.basename (rpms [0])
    spec_ver = re.match (r'(%s)-(\d+[.\d]*-\w+[.\w]*\.\w+)\.src\.rpm' % spec_base, srpm)
    if not spec_ver or spec_ver.lastindex != 2:
      raise Error ('Cannot deduce package version from `%s`' % rpms [0])

    srpm_name = spec_ver.group (1)
    ver_full = spec_ver.group (2)
    if srpm_name != spec_base:
      raise Error ('Package name in `%(srpm)s` does not match .spec name `%(spec_base)s`.\n'
                   'Either rename `%(spec_base)s.spec` to `%(srpm_name)s.spec` or set `Name:` tag to `%(spec_base)s`.'  % locals())

    # Generate ZIP.

    log_file = os.path.join (log_base, 'zip.log')
    log ('Creating ZIP (logging to %s)...' % log_file)

    zip_file = os.path.join (g_zip_dir, '%s-%s.zip' % (spec_base, ver_full.replace ('.', '_')))

    def gen_zip ():

      os.chdir (g_zip_dir)
      remove_path ('@unixroot')

      for r in base_rpms:
        log ('Unpacking `%s`...' % r)
        run_pipe ([[RPM2CPIO_EXE, r], [CPIO_EXE, '-idm']])

      remove_path (zip_file)
      log ('Creating `%s`...' % zip_file)
      run_pipe ([['zip', '-mry9', zip_file, '@unixroot']])

    func_log (log_file, gen_zip)


#
# Test command.
#

def test_cmd ():

  global g_test_cmds
  g_test_cmd_steps = {
    'all': ['-bb'],
    'prep': ['-bp', '--short-circuit'],
    'build': ['-bc', '--short-circuit'],
    'install': ['-bi', '--short-circuit'],
    'pack': ['-bb', '--short-circuit'],
  }

  opts = g_test_cmd_steps [g_args.STEP]

  for spec in g_args.SPEC.split (','):

    config = copy.deepcopy (g_config)
    full_spec, spec_base, spec_aux_dir = resolve_spec (spec, g_spec_dirs, config)

    if g_args.STEP == 'all':
      build_prepare (full_spec, spec_base)

    log_file = os.path.join (g_log_dir, 'test',
                             spec_base + ('' if g_args.STEP == 'all' else '.' + g_args.STEP) + '.log')

    rotate_log (log_file)

    base_arch = config.getwords ('general:archs') [0]

    log ('Creating test RPMs for `%(base_arch)s` target (logging to %(log_file)s)...' % locals ())

    rpms = run_log (log_file, [RPMBUILD_EXE, '--target=%s' % base_arch, '--define=dist %nil',
                               '--define=_sourcedir %s' % spec_aux_dir] + opts + [full_spec],
                    r'^Wrote: +(.+\.(?:%s|noarch)\.rpm)$' % base_arch)

    # Show the generated RPMs when appropriate.
    if g_args.STEP == 'all' or g_args.STEP == 'pack':
      if len (rpms):
        log ('Successfully generated the following RPMs:')
        log ('\n'.join (rpms))
      else:
        raise Error ('Cannot find `.(%(base_arch)s|noarch).rpm` file names in `%(log_file)s`.' % locals ())

#
# Main
#

# Script's start timestamp.
g_start_ts = datetime.datetime.now ()

# Script's own log file.
g_log = None

# Cache of RPM macro values.
g_rpm = {}

# RPM macros to pre-evaluate.
g_rpmbuild_used_macros = ['_topdir', '_sourcedir', 'dist', '_bindir']

# Script's output redirection (for #func_log).
g_output_file = None

# Parse command line.

g_cmdline = argparse.ArgumentParser (formatter_class = argparse.ArgumentDefaultsHelpFormatter)
g_cmdline.add_argument ('--version', action = 'version', version = '%(prog)s ' + VERSION)
g_cmdline.add_argument ('-c', action = 'store_true', dest = 'log_to_console', help = 'log everything to console')

g_cmds = g_cmdline.add_subparsers (metavar = 'COMMAND', help = 'command to run:')

g_cmd_test = g_cmds.add_parser ('test',
  help = 'test build (one arch)', description = '''
Run a test build of SPEC for one architecture. STEP may speficty a rpmbuild
shortcut to go to a specific build step.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_test.add_argument ('STEP', nargs = '?', choices = ['all', 'prep', 'build', 'install', 'pack'], default = 'all', help = 'build step: %(choices)s', metavar = 'STEP')
g_cmd_test.add_argument ('SPEC', help = 'spec file (comma-separated if more than one)')
g_cmd_test.set_defaults (cmd = test_cmd)

g_cmd_build = g_cmds.add_parser ('build',
  help = 'normal build (all configured archs)', description = '''
Build SPEC for all configured architectures.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_build.add_argument ('SPEC', help = 'spec file (comma-separated if more than one)')
g_cmd_build.set_defaults (cmd = build_cmd)

g_args = g_cmdline.parse_args ()

# Read the main config file.

g_config = Config (g_rpm)
g_config.read (os.path.expanduser ('~/rpmbuild-bot2.ini'))

g_spec_dirs = []

for d in g_config.getlines ('general:spec_dirs'):
  if d [0] == '+' and len (g_spec_dirs):
    g_spec_dirs [-1].append (d [1:].lstrip ())
  else:
    g_spec_dirs.append ([d])

rc = 0

try:

  # Pre-evaluate some RPMBUILD macros (this will also chedk for RPMBUILD_EXE availability).

  for i, m in enumerate (subprocess.check_output ([
    RPMBUILD_EXE, '--eval',
    ''.join ('|%{?' + s + '}' for s in g_rpmbuild_used_macros).lstrip ('|')
  ]).strip ().split ('|')):
    g_rpm [g_rpmbuild_used_macros [i]] = m

  for m in ['_topdir', '_sourcedir']:
    if not g_rpm [m] or not os.path.isdir (g_rpm [m]):
      raise Error ('Value of `%%%s` in rpmbuild is `%s` and not a directory' % (m, g_rpm [m]))

  # Prepare some (non-rpmbuild-standard) directories.

  g_zip_dir = os.path.join (g_rpm ['_topdir'], 'zip')
  g_log_dir = os.path.join (g_rpm ['_topdir'], 'logs')

  for d in [g_zip_dir] + [os.path.join (g_log_dir, f) for f in ('test', 'build')]:
    ensure_dir (d)

  # Create own log file unless redirected to a file.

  if sys.stdout.isatty():
    d = os.path.join (g_log_dir, SCRIPT_LOG_FILE)
    rotate_log (d)
    g_log = open (d, 'w', buffering = 1)
  else:
    g_log = sys.stdout

  g_log.write ('[%s, %s]\n' % (g_start_ts.strftime (DATETIME_FMT), ' '.join (sys.argv)))

  # Run command.

  g_args.cmd ()

except ConfigParser.NoOptionError as e:

  log_err ('config', str (e))
  rc = 1

except RunError as e:

  msg = 'The following command failed with %s:\n  %s' % (e.msg, e.cmd)
  if e.log_file:
    msg += '\nInspect `%s` for more info.' % e.log_file
  log_err (e.prefix, msg)
  rc = e.code

except Error as e:

  log_err (e.prefix, e.msg)
  rc = e.code

except:

  log_err ('Unexpected exception occured:')
  log (traceback.format_exc ())
  rc = 127

finally:

  end_ts = datetime.datetime.now ()
  elapsed = str (end_ts - g_start_ts).rstrip ('0')

  if g_log != sys.stdout:
    sys.stdout.write ('%s (%s s).\n' % (rc and 'Failed with exit code %s' % rc or 'Succeeded', elapsed))

  # Finalize own log file.
  if g_log:
    g_log.write ('[%s, exit code %d, %s s]\n\n' % (end_ts.strftime (DATETIME_FMT), rc, elapsed))
    g_log.close ()

exit (rc)
