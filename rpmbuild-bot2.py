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

SCRIPT_INI_FILE = 'rpmbuild-bot2.ini'
SCRIPT_LOG_FILE = 'rpmbuild-bot2.log'

DATETIME_FMT = '%Y-%m-%d %H:%M:%S'


import sys, os, re, copy, argparse, ConfigParser, subprocess, datetime, traceback


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

  def get (self, section, option, raw = False, vars = None):
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

  def getlist (self, section, option, sep):
    return filter (None, self.get (section, option).split (sep))

  def getlines (self, section, option): return self.getlist (section, option, '\n')

  def getwords (self, section, option): return self.getlist (section, option, None)


#
# Error exception for this script.
#

class Error (BaseException):
  def __init__ (self, msg, log_file):
    self.log_file = log_file
    BaseException.__init__ (self, msg)


#
# Logs a message to the console and optionally to a file.
#

def log (msg):

  if msg [-1] != '\n':
    msg += '\n'
  sys.stdout.write (msg)

  # Note: log to the file only when console is not redirected.
  if g_log and g_log != sys.stdout:
    g_log.write (msg)


#
# Same as log but prepends ERROR: to the message.
#

def log_err (msg):

  if msg [-1] != '.':
    msg += '.'
  log ('ERROR: ' + msg)


#
# Runs a command and writes its output to a log file, optionally duplicating
# it to the console if g_args.log_to_console is set.
#
# Raises Error if execution fails or terminates with a non-zero exit code.
#

def run_log (log_file, command):

  with open (log_file, 'w', buffering = 1) as f:

    rc = 0
    start_ts = datetime.datetime.now ()
    f.write ('[%s, %s]\n' % (start_ts.strftime (DATETIME_FMT), ' '.join (command)))

    try:

      # Note: obey log_to_console only if console is not redirected.
      if g_args.log_to_console and g_log != sys.stdout:
        proc = subprocess.Popen (command, stdout = subprocess.PIPE, stderr = subprocess.STDOUT, bufsize = 1)
        for line in iter (proc.stdout.readline, ''):
          sys.stdout.write (line)
          f.write (line)
      else:
        proc = subprocess.Popen (command, stdout = f, stderr = subprocess.STDOUT, bufsize = 1)

      rc = proc.wait ()
      msg = 'exit code %d' % rc

    except OSError as e:

      rc = 1
      msg = 'error %d (%s)' % (e.errno, e.strerror)

    finally:

      end_ts = datetime.datetime.now ()
      elapsed = str (end_ts - start_ts).rstrip ('0')
      f.write ('[%s, %s, took %s s]\n' % (end_ts.strftime (DATETIME_FMT), msg, elapsed))

      if rc:
        raise Error ('The following command failed with %s:\n'
                     '  %s'
                     % (msg, ' '.join (command)),
                     log_file = log_file)


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
      raise Error ('Spec file `%s` is not found' % spec)
    else:
      raise Error ('Spec file `%s` is not found in %s' % (spec, spec_dirs))

  log ('Spec file: %s' % full_spec)
  log ('Spec source dir: %s' % spec_aux_dir)

  return (full_spec, spec_base, spec_aux_dir)


#
# Prepare for build and test commands. This includes the following:
#
# - Download legacy runtime libraries for the given spec if spec legacy is
#   configured.
#

def build_prepare (full_spec, spec_base):

  pass


#
# Build command.
#

def build_cmd ():

  config = copy.deepcopy (g_config)
  full_spec = resolve_spec (spec, g_spec_dirs, config)


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
                             spec_base + (g_args.STEP == 'all' and '' or '.' + g_args.STEP) + '.log')

    if os.path.exists (log_file):
      try: os.remove (log_file + '.bak')
      except OSError: pass
      os.rename (log_file, log_file + '.bak')

    base_arch = config.getwords ('general', 'archs') [0]

    log ('Doing test RPM build for `%(base_arch)s` target (logging to %(log_file)s)...' % locals ())

    run_log (log_file, [RPMBUILD_EXE, '--define=dist %nil', '--target=%s' % base_arch] + opts + [full_spec])


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

for d in g_config.getlines ('general', 'spec_dirs'):
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

  for d in [g_zip_dir, g_log_dir, os.path.join (g_log_dir, 'test')]:
    if not os.path.isdir (d): os.makedirs (d)

  # Create own log file unless redirected to a file.

  if sys.stdout.isatty():

    d = os.path.join (g_log_dir, SCRIPT_LOG_FILE)
    if os.path.exists (d):
      try: os.remove (d + '.bak')
      except OSError: pass
      os.rename (d, d + '.bak')

    g_log = open (d, 'w', buffering = 1)

  else:

    g_log = sys.stdout

  g_log.write ('[%s, %s]\n' % (g_start_ts.strftime (DATETIME_FMT), ' '.join (sys.argv)))

  # Run command.

  g_args.cmd ()

except ConfigParser.NoOptionError as e:

  log_err (str (e))
  rc = 1

except Error as e:

  msg = str (e)
  if e.log_file:
    msg += '\nInspect `%s` for more info.' % e.log_file
  log (msg)
  rc = 2

except:

  log (traceback.format_exc ())
  rc = 3

finally:

  end_ts = datetime.datetime.now ()
  elapsed = str (end_ts - g_start_ts).rstrip ('0')

  sys.stdout.write ('%s (took %s s).\n' % (rc and 'Failed with exit code %s' % rc or 'Succeeded', elapsed))

  # Finalize own log file.
  if g_log:
    g_log.write ('[%s, exit code %d, took %s s]\n\n' % (end_ts.strftime (DATETIME_FMT), rc, elapsed))
    g_log.close ()

exit (rc)
