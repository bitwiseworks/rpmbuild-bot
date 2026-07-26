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


RPMBUILD_EXE = 'rpmbuild.exe'
RPM2CPIO_EXE = 'rpm2cpio.exe'
CPIO_EXE = 'cpio.exe'


SCRIPT_INI_FILE = 'rpmbuild-bot2.ini'
SCRIPT_LOG_FILE = 'rpmbuild-bot2.log'

DATETIME_FMT = '%Y-%m-%d %H:%M:%S'

VER_FULL_REGEX = r'\d+[.\d\w]*-\w+[.\w]*\.\w+'
BUILD_USER_REGEX = r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+'


import argparse
import configparser
import copy
import datetime
import fnmatch
import getpass  # for user and hostname
import os
import re
import shutil
import socket  # for user and hostname
import subprocess
import sys
import textwrap
import time
import traceback

#
# -----------------------------------------------------------------------------
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
# - Support for `${RPM:<NAME>}` interpolation that is replaced with the value of
#   the <NAME> RPM macro.
# - Support for copy.deepcopy.
#
# Note: We leave this class in even for Python 3 because of the extensions we
# provide.
#

class Config (configparser.ConfigParser):

  def __init__ (self, rpm_macros, *args, **kwargs):

    self.get_depth = 0
    self.rpm_macros = rpm_macros
    configparser.ConfigParser.__init__ (self, *args, **kwargs)

    # Keep option names case-sensitive (vital for 'environment' section).
    self.optionxform = str

  def __deepcopy__ (self, memo):

    copy = Config (self.rpm_macros, defaults = self.defaults ())
    copy.rpm_macros = self.rpm_macros
    for s in self.sections ():
      copy.add_section (s)
      for (n, v) in self.items (s):
        copy.set (s, n, v)
    return copy

  def get (self, section, option = None, *, raw = False, vars = None, **kwargs):

    if not option:
      section, option = section.split (':')

    ret = super ().get (section, option, raw = True, vars = vars, **kwargs)
    if raw:
      return ret

    for f_section, f_option in re.findall (r'\$\{(\w+:)?((?<=SHELL:).+|\w+)\}', ret):
      self.get_depth = self.get_depth + 1
      if self.get_depth < configparser.MAX_INTERPOLATION_DEPTH:
        try:
          if f_section == 'ENV:':
            sub = os.environ.get (f_option)
            if not sub: raise configparser.NoOptionError (f_option, f_section [:-1])
          elif f_section == 'SHELL:':
            sub = shell_output (f_option).strip ()
          elif f_section == 'RPM:':
            if f_option not in self.rpm_macros:
              sub = command_output ([RPMBUILD_EXE, '--eval', f'%{{?{f_option}}}']).strip ()
              self.rpm_macros [f_option] = sub
            else:
              sub = self.rpm_macros [f_option]
          else:
            sub = self.get (f_section [:-1] or section, f_option, vars = vars)
          ret = ret.replace (f'${{{f_section}{f_option}}}', sub)
        except RunError as e:
          raise configparser.InterpolationError (section, option,
            f'Failed to interpolate ${{{f_section}{f_option}}}:\nThe following command failed with: {e.msg}:\n  {e.cmd}')
      else:
        raise configparser.InterpolationDepthError (option, section, ret)

    self.get_depth = self.get_depth - 1
    return ret

  def getlist (self, section, option = None, sep = None):
    return [v for v in self.get (section, option).split (sep) if v]

  def getlines (self, section, option = None): return self.getlist (section, option, '\n')

  def getwords (self, section, option = None): return self.getlist (section, option, None)


#
# -----------------------------------------------------------------------------
#
# Generic error exception for this script.
#
# If both prefix and msg are not None, then prefix followed by a colon is
# prepended to msg. Otherwise prefix is considered empty and either of them
# which is not None is treated as msg. The hint argument, if not None,
# specifies recommendations on how to fix the error. Note that hint must always
# go as a third argument (or be passed by name).
#

class Error (BaseException):
  code = 101
  def __init__ (self, prefix, msg = None, hint = None):
    self.prefix = prefix if msg else None
    self.msg = msg if msg else prefix
    self.hint = hint
    BaseException.__init__ (self, (self.prefix and self.prefix + ': ' or '') + self.msg)


#
# -----------------------------------------------------------------------------
#
# Error exception for #run and #run_pipe functions. See Error for more info.
#

class RunError (Error):
  code = 102
  def __init__ (self, cmd, msg, hint = None, log_file = None):
    self.cmd = cmd
    self.log_file = log_file
    Error.__init__ (self, msg, hint = hint)


#
# -----------------------------------------------------------------------------
#
# Returns a human readable string of float unix_time in local time zone.
#

def to_localtimestr (unix_time):
  return time.strftime ('%Y-%m-%d %H:%M:%S %Z', time.localtime (unix_time))


#
# -----------------------------------------------------------------------------
#
# Returns a human readable string of float unix_time in UTC.
#

def to_unixtimestr (unix_time):
  return time.strftime ('%Y-%m-%d %H:%M:%S UTC', time.gmtime (unix_time))

#
# -----------------------------------------------------------------------------
#
# Logs a message to the console and optionally to a file.
#
# If msg doesn't end with a new line terminator, it will be appended.
#

def log (msg, wrap_width = None, file_only = False):

  if wrap_width != None:
    if int (wrap_width) <= 0:
      wrap_width = 79
    msg = textwrap.fill (msg, wrap_width)

  if len (msg) == 0 or msg [-1] != '\n':
    msg += '\n'

  if g_output_file:

    g_output_file.write (msg)

    # Note: obey log_to_console only if the console is not redirected to a file.
    if g_args.log_to_console and g_output_file != sys.stdout and not file_only:
      sys.stdout.write (msg)

  else:

    if not file_only:
      sys.stdout.write (msg)

    # Note: log to the file only when the console is not redirected to it.
    if (g_log and g_log != sys.stdout) or file_only:
      g_log.write (msg)


#
# -----------------------------------------------------------------------------
#
# Same as log but prepends a string in kind followed by a colon to the message.
#
# The kind string should indicate the message kind (ERROR, HINT, INFO etc). If
# msg is None, prefix will be treated as msg. Otherwise, prefix will be put
# between kind and msg, followed by a colon.
#

def log_kind (kind, prefix, msg = None, **kwargs):

  if not msg:
    msg = prefix
    prefix = None

  while msg.startswith ('\n'):
    kind = '\n' + kind
    msg = msg [1:]

  log (f'{kind}: ' + (prefix and prefix + ': ' or '') + msg, **kwargs)


def log_err (prefix, msg = None, **kwargs):
  return log_kind ('ERROR', prefix, msg, **kwargs)


def log_warn (prefix, msg = None, **kwargs):
  return log_kind ('WARNING', prefix, msg, **kwargs)


def log_note (prefix, msg = None, **kwargs):
  return log_kind ('NOTE', prefix, msg, **kwargs)


def log_hint (prefix, msg = None, **kwargs):
  return log_kind ('HINT', prefix, msg, **kwargs)


#
# -----------------------------------------------------------------------------
#
# Prompts a user for an input and returns the result logging both the prompt
# and the user answer.
#
# Returns None if interrupted with Ctrl-Break or such.
#

def log_input (prompt, choice = None, kind = None):

  choice_upper = choice.upper () if choice else None

  kind_str = '' if not kind else f'{kind}: '
  choice_str = '' if not choice else f'[{choice}] '

  try:
    while True:
      answer = input (f'{kind_str}{prompt} {choice_str}')
      answer_upper = answer.upper ()
      answer_empty = answer == ''
      if not choice or (not answer_empty and answer_upper != choice_upper and answer_upper in choice_upper):
        log (f'{kind_str}{prompt} {choice_str}{answer}', file_only = True)
        return answer_upper
  except KeyboardInterrupt:
    return None


def log_input_warn (prompt, choice = None):
  return log_input (prompt, choice, kind = 'WARNING')

#
# -----------------------------------------------------------------------------
#
# Prepare a log file by backing up the previous one.
#

def rotate_log (log_file):

  if os.path.exists (log_file):

    bak_file = log_file + '.bak'

    if os.path.exists (bak_file):
      os.remove (bak_file)

    try:
      os.rename (log_file, bak_file)
    except OSError as e:
      # The file may be open, try to copy + truncate
      try:
        shutil.copyfile (log_file, bak_file)
        os.truncate (log_file, 0)
      except OSError:
        raise Error (f'Cannot rename `{log_file}` to `.bak`: {e}')

#
# -----------------------------------------------------------------------------
#
# Ensures path is a directory and exists.
#

def ensure_dir (path):

  try:
    if not os.path.isdir (path):
      os.makedirs (path)
  except OSError as e:
    raise Error (f'Cannot create directory `{path}`: {e}')


#
# -----------------------------------------------------------------------------
#
# Removes a file or a directory (including its contents) at path. Does not raise
# an exception if the path does not exist.
#
# If optional @c relaxed is True, then the "Resource busy" error will also be
# ignored. This is useful when deleting directories which the user may be
# currently staying at (e.g. when examining logs). Shall not be set to True
# when the directory must for sure not exist.
#

def remove_path (path, relaxed = False):

  try:
    if os.path.isfile (path):
      os.remove (path)
    elif os.path.isdir (path):
      try:
        shutil.rmtree (path)
      except OSError as e:
        if not relaxed or e.errno != 16:
          raise
  except OSError as e:
    if e.errno != 2:
      raise


#
# -----------------------------------------------------------------------------
#
# Runs a command in a separate process and captures its output.
#
# This is a simplified shortcut to subprocess.check_output that raises RunError
# on failure. Command must be a list where the first entry is the name of the
# executable. Command's stderr is discarded.
#
# Note that this function will raise subprocess.CalledProcessError if the
# command exits with a non-zero return code. To suppress this exception (and
# get the return code together with the output, use #command_output_rc.
#

def command_output (command, cwd = None):
  try:
    with open (os.devnull, 'w') as fNull:
      return subprocess.check_output (command, stderr = fNull, cwd = cwd, env = g_run_env, text = True)
  except subprocess.CalledProcessError as e:
    raise RunError (' '.join (command), f'Non-zero exit status {e.returncode}')
  except OSError as e:
    raise RunError (' '.join (command), str (e))


#
# -----------------------------------------------------------------------------
#
# Runs a shell command in a separate process and captures its output.
#
# This is a simplified shortcut to subprocess.check_output that raises RunError
# on failure. Command must be a string representing a shell command. Command's
# or shell's stderr is discarded.
#
# Note that this function will raise subprocess.CalledProcessError if the
# command exits with a non-zero return code. To suppress this exception (and
# get the return code together with the output, use #shell_output_rc.
#

def shell_output (command, cwd = None):
  try:
    with open (os.devnull, 'w') as fNull:
      return subprocess.check_output (command, stderr = fNull, shell = True, cwd = cwd, env = g_run_env, text = True)
  except subprocess.CalledProcessError as e:
    raise RunError (' '.join (command), f'Non-zero exit status {e.returncode}')
  except OSError as e:
    raise RunError (' '.join (command), str (e))


#
# -----------------------------------------------------------------------------
#
# Same as #command_output but returns a tuple where the second value is
# a command exit code.
#

def command_output_rc (command, cwd = None):
  try:
    with open (os.devnull, 'w') as fNull:
      return subprocess.check_output (command, stderr = fNull, cwd = cwd, env = g_run_env, text = True), 0
  except subprocess.CalledProcessError as e:
    return e.output, e.returncode


#
# -----------------------------------------------------------------------------
#
# Same as #shell_output but returns a tuple where the second value is
# a shell exit code.
#

def shell_output_rc (command, cwd = None):
  try:
    with open (os.devnull, 'w') as fNull:
      return subprocess.check_output (command, stderr = fNull, shell = True, cwd = cwd, env = g_run_env, text = True), 0
  except subprocess.CalledProcessError as e:
    return e.output, e.returncode


#
# -----------------------------------------------------------------------------
#
# Same as #command_output but suppresses all command's output and returns its
# exit code.
#

def command_rc (command, cwd = None):
  try:
    with open (os.devnull, 'w') as fNull:
      return subprocess.call (command, stdout = fNull, stderr = fNull, cwd = cwd, env = g_run_env)
  except OSError as e:
    raise RunError (' '.join (command), str (e))


#
# -----------------------------------------------------------------------------
#
# Same as #shell_output but suppresses all shell's output and returns its
# exit code.
#

def shell_rc (command, cwd = None):
  try:
    with open (os.devnull, 'w') as fNull:
      return subprocess.call (command, stdout = fNull, stderr = fNull, shell = True, cwd = cwd, env = g_run_env)
  except OSError as e:
    raise RunError (' '.join (command), str (e))


#
# -----------------------------------------------------------------------------
#
# Same as #command_output but logs the command before executing it and does not
# capture or suppress command's stdout or stderr.
#

def command (command, cwd = None):
  command_str = ' '.join (command)
  try:
    log (f'Running `{command_str}`...')
    subprocess.check_call (command, cwd = cwd, env = g_run_env)
  except subprocess.CalledProcessError as e:
    raise RunError (command_str, f'Non-zero exit status {e.returncode}')
  except OSError as e:
    raise RunError (command_str, str (e))


#
# -----------------------------------------------------------------------------
#
# Same as #shell_output but logs the command before executing it and does not
# capture or suppress shell's or command's stdout or stderr.
#

def shell (command, cwd = None):
  command_str = ' '.join (command)
  try:
    log (f'Running [{command_str}]...')
    subprocess.check_call (command, shell = True, cwd = cwd, env = g_run_env)
  except subprocess.CalledProcessError as e:
    raise RunError (command_str, f'Non-zero exit status {e.returncode}')
  except OSError as e:
    raise RunError (command_str, str (e))


#
# -----------------------------------------------------------------------------
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

def run_pipe (commands, regex = None, file = None, cwd = None):

  if not file:
    file = g_output_file

  recomp = re.compile (regex) if regex else None
  lines = []
  rc = 0

  # Note: obey log_to_console only if the console is not redirected to a file.
  # Also makes no sense to capture if file equals to sys.stdout (unless regex
  # is given). If file is None, we have to capture to hide any output at all.
  duplicate_output = g_args.log_to_console and file != sys.stdout
  capture_output = duplicate_output or bool (recomp) or not file
  try:

    cmd = commands [0]

    if len (commands) == 1:

      if capture_output:
        proc = subprocess.Popen (cmd, stdout = subprocess.PIPE, stderr = subprocess.STDOUT, text = True, cwd = cwd, env = g_run_env)
        capture_file = proc.stdout
      else:
        proc = subprocess.Popen (cmd, stdout = file, stderr = subprocess.STDOUT, text = True, cwd = cwd, env = g_run_env)

    else:

      if capture_output:
        # Note: We can't use proc.stderr here as it's only a read end.
        rpipe, wpipe = os.pipe ()
        capture_file = os.fdopen (rpipe)
      else:
        wpipe = file

      proc = subprocess.Popen (cmd, stdout = subprocess.PIPE, stderr = wpipe, cwd = cwd, env = g_run_env)

      last_proc = proc
      for cmd in commands [1:]:
        last_proc = subprocess.Popen (cmd, stdin = last_proc.stdout,
                                      stdout = wpipe if cmd == commands [-1] else subprocess.PIPE,
                                      stderr = wpipe, cwd = cwd, env = g_run_env)

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
    msg = f'exit code {rc}'

  except OSError as e:

    rc = 1
    msg = f'error {e.errno} {e.strerror}'

  finally:

    if rc:
      raise RunError (' '.join (cmd), msg)

  return lines


#
# -----------------------------------------------------------------------------
#
# Shortcut to #run_pipe for one command.
#

def run (command, regex = None, cwd = None):
  return run_pipe ([command], regex, cwd = cwd)


#
# -----------------------------------------------------------------------------
#
# Similar to #run_pipe but all output produced by and external commands will be
# be redirected to a log file.
#

def run_pipe_log (log_file, commands, regex = None, cwd = None):

  with open (log_file, 'w', buffering = 1) as f:

    start_ts = datetime.datetime.now ().astimezone ()
    f.write (f'[{start_ts.strftime (DATETIME_FMT)}, {' | '.join (' '.join(c) for c in commands)}]\n')

    try:

      rc = 0
      msg = 'exit code 0'
      lines = run_pipe (commands, regex, f, cwd = cwd)

    except RunError as e:

      rc = 1
      cmd = e.cmd
      msg = e.msg

    finally:

      end_ts = datetime.datetime.now ().astimezone ()
      elapsed = str (end_ts - start_ts).rstrip ('0')
      f.write (f'[{end_ts.strftime (DATETIME_FMT)}, {msg}, {elapsed} s]\n')

      if rc:
        raise RunError (cmd, msg, log_file = log_file)

  return lines


#
# -----------------------------------------------------------------------------
#
# Shortcut to #run_pipe_log for one command.
#

def run_log (log_file, command, regex = None):
  return run_pipe_log (log_file, [command], regex)


#
# -----------------------------------------------------------------------------
#
# Similar to #run_log but runs a Python function. All output produced by #log
# and external commands run via #run and #run_pipe within this function will be
# redirected to a log file.
#

def func_log (log_file, func):

  with open (log_file, 'w', buffering = 1) as f:

    start_ts = datetime.datetime.now ().astimezone ()
    f.write (f'[{start_ts.strftime (DATETIME_FMT)}, Python {func!s}]\n')

    try:

      # Cause redirection of #log to the given file.
      global g_output_file
      g_output_file = f

      rc = func () or 0
      msg = f'return code {rc}'

    except RunError as e:

      rc = 1
      f.write (f'{e.cmd}: {e.msg}\n')

    except:  # noqa: E722 (we log below)

      rc = 1
      f.write (f'Unexpected exception occurred:\n{traceback.format_exc ()}')

    finally:

      g_output_file = None

      if rc:
        msg = 'exception ' + sys.exc_type.__name__

      end_ts = datetime.datetime.now ().astimezone ()
      elapsed = str (end_ts - start_ts).rstrip ('0')
      f.write (f'[{end_ts.strftime (DATETIME_FMT)}, {msg}, {elapsed} s]\n')

      if rc:
        raise RunError (str (func), msg, log_file = log_file)


#
# -----------------------------------------------------------------------------
#
# Returns a VCS type if the given path is in a tree which is under version
# control, or None. Supported types are `git` and `svn`.
#

def get_vcs_type (path):

  if os.path.isfile (path):
    path = os.path.dirname (path)
  if not os.path.isdir (path):
    return None

  path = os.path.abspath (path)

  output, rc = command_output_rc (['git', 'rev-parse', '--is-inside-work-tree'], cwd = path)
  if rc == 0 and output.strip () == 'true':
    return 'git'

  # With SVN, things are more complicated.
  while True:
    rc = command_rc (['svn', 'info'], cwd = path)
    if rc == 0:
      return 'svn'
    if path.endswith (os.sep):
      break
    path = os.path.dirname (path)

  return None


#
# -----------------------------------------------------------------------------
#
# Searches for a spec file in the provided path or in spec_dirs if no path is
# provided in spec. Assumes the `.spec` extension if it is missing. If the spec
# file is found, this function will do the following:
#
# - Load SCRIPT_INI_FILE into config if it exists in a spec_dirs directory
#   containing the spec file (directly or through children).
# - Load SCRIPT_INI_FILE into config if it exists in the same directory
#   where the spec file is found.
# - Log the name of the found spec file.
# - Return a tuple with the full path to the spec file, spec base name (w/o
#   path or extension) and full path to the auxiliary source directory for this
#   spec.
# - Set the global g_run_env variable to the environment found in the INI file
#   (if any), joined with the system environment.
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
      raise Error (f'Cannot find `{spec}`')
    else:
      raise Error (f'Cannot find `{spec}` in {spec_dirs}')

  log (f'Spec file       : {full_spec}')
  log (f'Spec source dir : {spec_aux_dir}')

  # Validate some mandatory config options.
  if not config.get ('general:archs'):
    raise Error ('config', 'No value for option `general:archs`')

  # Load the environment.
  g_run_env = copy.deepcopy (os.environ)
  if config.has_section ('environment'):
    for var in config.options ('environment'):
      g_run_env [var] = config.get ('environment', var)

  return (full_spec, spec_base, spec_aux_dir)


#
# -----------------------------------------------------------------------------
#
# Reads settings of a given repository group from a given config and returns a
# dictionary with the following keys:
#
# - base: base directory of the group;
# - repos: list of group's repositories;
# - repo.REPO (a value from repos): dictionary with the following keys:
#   - layout: repo layout's name;
#   - base: base directoy of the repo (with group's base prepended);
#   - rpm, srpm, zip, log: directories of respective parts as defined by repo's
#     layout (with repo's base prepended).
#
# Besides repo.REPO for each repository from the group's repository list, there
# is also a special key `repos.None` (where None is a None constant rather a
# string). This key contains the respective local build directories where
# rpmbuild puts RPMs.
#

def read_group_config (group, config):

  d = {}

  if group:

    group_section = f'group.{group}'
    d ['base'] = config.get (group_section, 'base')
    d ['repos'] = config.getwords (group_section, 'repositories')

    if len (d ['repos']) == 0:
      raise Error ('config', f'No repositories in group `{group}`')

    for repo in d ['repos']:

      rd = {}

      repo_section = f'repository.{group}.{repo}'
      rd ['layout'] = config.get (repo_section, 'layout')
      rd ['base'] = repo_base = os.path.join (d ['base'], config.get (repo_section, 'base'))

      layout_section = f'layout.{rd ['layout']}'
      rd ['rpm'] = os.path.join (repo_base, config.get (layout_section, 'rpm'))
      rd ['srpm'] = os.path.join (repo_base, config.get (layout_section, 'srpm'))
      rd ['zip'] = os.path.join (repo_base, config.get (layout_section, 'zip'))
      rd ['log'] = os.path.join (repo_base, config.get (layout_section, 'log'))

      d [f'repo.{repo}'] = rd

  ld = {}
  ld ['base'] = g_rpm ['_topdir']
  ld ['rpm'] = g_rpm ['_rpmdir']
  ld ['srpm'] = g_rpm ['_srcrpmdir']
  ld ['zip'] = g_zip_dir
  ld ['log'] = os.path.join (g_log_dir, 'build')

  d [f'repo.{None}'] = ld

  return d


#
# -----------------------------------------------------------------------------
#
# Returns a resolved path of a given file in a given repo.
#
# None as repo means the local build. Otherwise, it's a repo name from the INI
# file and group_config must also be not None and represent this repo's group.
#

def resolve_path (name, arch, repo = None, group_config = None):

  if arch in ['srpm', 'zip']:
    path = os.path.join (group_config [f'repo.{repo}'] [arch], name)
  else:
    path = os.path.join (group_config [f'repo.{repo}'] ['rpm'], arch, name)

  return path


#
# -----------------------------------------------------------------------------
#
# Exception for #read_build_summary to let callers customize the error when
# a summary file is missing.
#

class NoBuildSummary (BaseException):
  def __init__ (self, summary):
    self.summary = summary
    BaseException.__init__ (self, str (summary))


#
# -----------------------------------------------------------------------------
#
# Reads the build summary file of spec_base located in a given group and
# returns the following as a tuple:
#
# - Full version as was defined by the spec.
#
# - Name of the user who built the spec followed by `@` and the hostname (both
# are non-empty strings).
#
# - Timestamp of the build.
#
# - Dict containing resolved file names of all RPM files built from the spec.
# The dict has the following keys: 'srpm', 'zip' and one key per each built
# arch. The first two keys contain a single file name. Each of the arch keys
# contains a list of file names.
#
# - List of move operations for this build where each entry is a list with the
# following items: target repository, who moved (name@machine), unix time of
# move.
#
# Passing None as group will read the summary from the local build directory.
# Otherwise, it must be a repository group name from the INI file and config
# must also be not None. In this case a summary file of the corresponding
# repository will be accessed.
#
# This method performs integrity checking of the summary file (version string
# validity, existence of files, their timestamps etc.) and raises an Error on
# any failure.
#

def read_build_summary (spec_base, ver, repo, group_config):

  log_base = os.path.join (group_config [f'repo.{repo}'] ['log'], spec_base)

  if ver:
    log_base = os.path.join (log_base, ver)

  try:

    summary = os.path.join (log_base, 'summary')
    with open (summary, 'r') as f:

      try:

        ln = 1
        ver_full = f.readline ().strip ()
        if not re.match (rf'^{VER_FULL_REGEX}$', ver_full):
          raise Error (f'Invalid version specification: `{ver_full}`')

        ln = 2
        build_user, build_time = f.readline ().strip ().split ('|')
        if not re.match (rf'^{BUILD_USER_REGEX}$', build_user):
          raise Error (f'Invalid build user specification: `{build_user}`')
        build_time = float (build_time)

        rpms = {}
        hist = []

        for line in f:

          ln += 1

          if line.startswith ('>'):
            # Parse move history.
            move_repo, move_user, move_time = line.split ('|')
            move_repo = move_repo.lstrip('>')
            hist.append ([move_repo, move_user, float (move_time)])
            continue

          arch, name, mtime, size = line.strip ().split ('|')
          mtime = float (mtime)
          size = int (size)
          path = resolve_path (name, arch, repo, group_config)

          if os.path.getmtime (path) != mtime:
            raise Error (f'{summary}:{ln}', f'Recorded mtime differs from actual for `{path}`')
          if os.path.getsize (path) != size:
            raise Error (f'{summary}:{ln}', f'Recorded size differs from actual for `{path}`')

          if arch in ['srpm', 'zip']:
            rpms [arch] = path
          else:
            if arch in rpms:
              rpms [arch].append (path)
            else:
              rpms [arch] = [path]

      except OSError as e:
        raise Error (f'{summary}:{ln}:\n{e}')

      except ValueError:
        raise Error (f'{summary}:{ln}', 'Invalid field type or number of fields')

    return ver_full, build_user, build_time, rpms, hist

  except OSError as e:
    if e.errno == 2:
      raise NoBuildSummary (summary)
    else:
      raise Error (f'Cannot read build summary for `{spec_base}`:\n{e}')


#
# -----------------------------------------------------------------------------
#
# Exception for #read_build_summary to let callers customize the error when
# a summary file is missing.
#

class CommandCancelled (BaseException):
  def __init__ (self):
    BaseException.__init__ (self, 'Command cancelled')


#
# -----------------------------------------------------------------------------
#
# Get a list of archs to build for spec.
#

def get_spec_archs (config, spec_base):

  key = 'specs.archs'
  if config.has_option (key, spec_base):
    archs = config.getwords (key, spec_base)
    if len (archs) < 1:
      raise Error ('config', f'No value for option `{key}:{spec_base}`');
    return archs

  return config.getwords ('general:archs')


#
# -----------------------------------------------------------------------------
#
# Get the base arch for spec.
#

def get_basespec_arch (config, spec_base):

  base_arch = None
  archs = get_spec_archs (config, spec_base)
  for arch in archs:
    if not base_arch:
      base_arch = arch
      break
  return base_arch

#
# -----------------------------------------------------------------------------
#
# Prepare for build and test commands. This includes the following:
#
# - Copy files from spec_aux_dir to source_dir (to be used as an override for
# _sourcedir when calling rpmbuild for full_spec).
#
# - Download legacy runtime libraries for the given spec if spec legacy is
# configured (TODO: Actually implement it).
#

def build_prepare (full_spec, spec_base, spec_aux_dir, source_dir, archs, config):

  ensure_dir (source_dir)

  # Copy all files from aux dir but spec itself.

  for f in os.listdir (spec_aux_dir):
    ff = os.path.join (spec_aux_dir, f)
    if os.path.samefile (ff, full_spec):
      continue
    shutil.copy2 (ff, source_dir)

  # Get legacy runtime.

  legacy_key = 'specs.legacy'
  if config.has_option (legacy_key, spec_base):

    group, repo = (config.get ('general', 'legacy.repository').split (':', 1) + [None]) [:2]
    if not group or not repo:
      raise Error ('config', 'Invalid value for option `general:legacy.repository`')

    group_config = read_group_config (group, config)
    try:
      repo_config = group_config [f'repo.{repo}']
    except KeyError:
      raise Error (f'No repository `{repo}` listed in configured group `{group}`')

    rpm_list = config.getwords (legacy_key, spec_base)
    if len (rpm_list) < 1:
      raise Error ('config', f'No value for option `{legacy_key}:{spec_base}`');

    abi_list = []

    for rpm_spec in rpm_list:

      try:
        abi, name, ver, mask, legacy_arch = (rpm_spec.split ('|') + [None, None]) [:5]
      except ValueError:
        raise Error ('config', f'Invalid number of fields for option `{legacy_key}:{spec_base}`')
      if '' in [abi, name, ver]:
        raise Error ('config', f'Invalid field values for option `{legacy_key}:{spec_base}`')

      if not mask:
        mask = '*.dll'

      # Add the dist suffix, if any, to ver (to make it consistent).
      ver += g_rpm ['dist']

      abi_list.append (abi)

      # Enumerate RPMs for all archs and extract them.
      log (f'Getting legacy runtime ({mask}) for ABI {abi}...')
      for arch in [legacy_arch] if legacy_arch else archs:

        rpm = os.path.join (repo_config ['rpm'], arch, f'{name}-{ver}.{arch}.rpm')
        tgt_dir = os.path.join (source_dir, f'{spec_base}-legacy', abi, arch)

        # Check filenames and timestamps
        log (f'Checking package {rpm}...')
        if not os.path.isfile (rpm):
          raise Error (f'File not found: {rpm}')

        ts = os.path.getmtime (rpm)

        old_ts, old_rpm, old_name, old_ver = [None, None, None, None]
        tgt_list = tgt_dir + '.list'
        if os.path.isfile (tgt_list):
          with open (tgt_list, 'r') as l:
            try:
              old_ts, old_rpm, old_name, old_ver = l.readline ().strip ().split ('|')
              old_ts = float (old_ts)
            except ValueError:
              raise Error (rpm, 'Incorrect number of fields')

        if old_ts != ts or old_rpm != rpm or old_name != name or old_ver != ver:
          log (f'Extracting to {tgt_dir}...')
          remove_path (tgt_list)
          remove_path (tgt_dir)
          ensure_dir (tgt_dir)
          os.chdir (tgt_dir)
          run_pipe ([[RPM2CPIO_EXE, rpm], [CPIO_EXE, '-idm', mask]])
          # Save the file list for later use.
          all_files = []
          with open (tgt_dir + '.files.list', 'w') as l:
            for root, dirs, files in os.walk (tgt_dir):
              for f in files:
                f = os.path.join (root [len (tgt_dir):], f)
                all_files.append (f)
                l.write (f + '\n')
          # Now try to locate the debuginfo package and extract *.dbg from it.
          debug_rpm = os.path.join (repo_config ['rpm'], arch, f'{name}-debuginfo-{ver}.{arch}.rpm')
          have_debug_rpm = os.path.isfile (debug_rpm)
          if not have_debug_rpm:
            debug_rpm = os.path.join (repo_config ['rpm'], arch, f'{name}-debug-{ver}.{arch}.rpm')
            have_debug_rpm = os.path.isfile (debug_rpm)
          if have_debug_rpm:
            log (f'Found debug info package {debug_rpm}, extracting...')
            # Save the file for later inclusion into debugfiles.list (%debug_package magic in brp-strip-os2).
            dbgfilelist = tgt_dir + '.debugfiles.list'
            remove_path (dbgfilelist)
            masks = []
            with open (dbgfilelist, 'w') as l:
              for f in all_files:
                f = os.path.splitext (f) [0] + '.dbg'
                l.write (f + '\n')
                masks.append ('*' + f)
            run_pipe ([[RPM2CPIO_EXE, debug_rpm], [CPIO_EXE, '-idm'] + masks])
          # Put the 'done' mark.
          with open (tgt_list, 'w') as l:
            l.write (f'{ts}|{rpm}|{name}|{ver}\n')

        with open (os.path.join (source_dir, f'{spec_base}-legacy', 'abi.list'), 'w') as l:
          l.write (' '.join (abi_list) + '\n')

#
# -----------------------------------------------------------------------------
#
# Build command.
#

def build_cmd ():

  for spec in g_args.SPEC.split (','):

    config = copy.deepcopy (g_config)
    full_spec, spec_base, spec_aux_dir = resolve_spec (spec, g_spec_dirs, config)

    archs = get_spec_archs (config, spec_base)
    source_dir = os.path.join (g_rpm ['_sourcedir'], spec_base)

    build_prepare (full_spec, spec_base, spec_aux_dir, source_dir, archs, config)

    log ('Targets: ' + ', '.join (archs) + f', ZIP ({archs [0]}), SRPM')

    log_base = os.path.join (g_log_dir, 'build', spec_base)

    summary = os.path.join (log_base, 'summary')
    if os.path.isfile (summary):
      with open (summary, 'r') as f:
        ver = f.readline ().strip ()
      if g_args.force_command:
        log_note (f'Overwriting previous build of `{spec_base}` ({ver}) due to -f option.')
      else:
        raise Error (f'Build summary for `{spec_base}` version {ver} already exists: {summary}',
          hint = 'Use -f option to overwrite this build with another one w/o uploading it.')

    remove_path (log_base, relaxed = True)
    ensure_dir (log_base)

    # Generate RPMs for all architectures.

    base_rpms = None
    arch_rpms = {}
    noarch_rpms = {}

    for arch in archs:

      log_file = os.path.join (log_base, f'{arch}.log')
      log (f'Creating RPMs for `{arch}` target (logging to {log_file})...')

      rpms = run_log (log_file,
        [RPMBUILD_EXE, f'--target={arch}', '-bb', f'--define=_sourcedir {source_dir}', full_spec],
        rf'^Wrote: +(.+\.(?:{arch}|noarch)\.rpm)$')

      if len (rpms):
        # Save the base arch RPMs for later.
        if not base_rpms:
          base_rpms = rpms
        # Deal with noarch.
        arch_only = []
        for r in rpms:
          if r.endswith ('.noarch.rpm'):
            noarch_rpms [r] = True
          else:
            arch_only.append (r)
        if len (arch_only) == 0:
          log (f'Skipping other targets because `{arch}` produced only `noarch` RPMs.')
          break
        arch_rpms [arch] = arch_only
      else:
        raise Error (f'Cannot find `.({arch}|noarch).rpm` file names in `{log_file}`.')

    arch_rpms ['noarch'] = noarch_rpms.keys ()

    # Generate SRPM.

    log_file = os.path.join (log_base, 'srpm.log')
    log (f'Creating SRPM (logging to {log_file})...')

    srpm = run_log (log_file,
      [RPMBUILD_EXE, '-bs', f'--define=_sourcedir {source_dir}', full_spec],
      r'^Wrote: +(.+\.src\.rpm)$') [0]

    if not srpm:
      raise Error (f'Cannot find `.src.rpm` file name in `{log_file}`.')

    # Find package version.

    srpm_base = os.path.basename (srpm)
    spec_ver = re.match (rf'({spec_base})-({VER_FULL_REGEX})\.src\.rpm', srpm_base)
    if not spec_ver or spec_ver.lastindex != 2:
      raise Error (f'Cannot deduce package version from `{srpm}` with spec_base `{spec_base}`',
        hint = f'Check that Release value ends with %{{?dist}} macro in `{spec_base}`.spec')

    srpm_name = spec_ver.group (1)
    ver_full = spec_ver.group (2)
    if srpm_name != spec_base:
      raise Error (f'Package name in `{srpm_base}` does not match .spec name `{spec_base}`.\n'
        f'Either rename `{spec_base}.spec` to `{srpm_name}.spec` or set `Name:` tag to `{spec_base}`.')

    # Generate ZIP.

    log_file = os.path.join (log_base, 'zip.log')
    log (f'Creating ZIP (logging to {log_file})...')

    zip_file = os.path.join (g_zip_dir, f'{spec_base}-{ver_full.replace ('.', '_')}.zip')

    def gen_zip (base_rpms = base_rpms, zip_file = zip_file):

      os.chdir (g_zip_dir)
      remove_path ('@unixroot')

      for r in base_rpms:
        log (f'Unpacking `{r}`...')
        run_pipe ([[RPM2CPIO_EXE, r], [CPIO_EXE, '-idm']])

      remove_path (zip_file)
      log (f'Creating `{zip_file}`...')
      run_pipe ([['zip', '-mry9', zip_file, '@unixroot']])

    func_log (log_file, gen_zip)

    # Write a summary with all generated packages for further reference.

    def file_data (path):
      return f'{os.path.basename (path)}|{os.path.getmtime (path)}|{os.path.getsize (path)}'

    with open (f'{summary}.tmp', 'w') as f:
      f.write (ver_full + '\n')
      f.write (f'{g_username}@{g_hostname}|{time.time ()}\n')
      f.write (f'srpm|{file_data (srpm)}\n')
      f.write (f'zip|{file_data (zip_file)}\n')
      for arch, files in arch_rpms.items ():
        for r in files:
          f.write (f'{arch}|{file_data (r)}\n')

    # Everything succeeded.
    os.rename (f'{summary}.tmp', summary)
    log (f'Generated all packages for version {ver_full}.')


#
# -----------------------------------------------------------------------------
#
# Test command.
#

def test_cmd ():

  purge = g_args.STEP == 'purge'

  g_test_cmd_steps = {
    'prep': ['-bp', '--short-circuit'],
    'build': ['-bc', '--short-circuit'],
    'install': ['-bi', '--short-circuit'],
    'pack': ['-bb', '--short-circuit'],
    'all': ['-bb'],
  }

  if not purge:
    opts = g_test_cmd_steps [g_args.STEP]

  for spec in g_args.SPEC.split (','):

    config = copy.deepcopy (g_config)
    full_spec, spec_base, spec_aux_dir = resolve_spec (spec, g_spec_dirs, config)

    archs = get_spec_archs (config, spec_base)
    base_arch = archs [0]

    source_dir = os.path.join (g_rpm ['_sourcedir'], spec_base)
    if g_args.STEP in ['all', 'install']:
      build_prepare (full_spec, spec_base, spec_aux_dir, source_dir, archs, config)

    log_base = os.path.join (g_log_dir, 'test', spec_base)
    if not purge:
      ensure_dir (log_base)

    if purge:

      rpms = set ()
      for l in ['all', 'pack']:
        lf = os.path.join (log_base, l + '.log')
        if os.path.isfile (lf):
          with open (lf, 'r') as f:
            for line in iter (f.readline, ''):
              for r in re.findall (rf'^Wrote: +(.+\.(?:{base_arch}|noarch)\.rpm)$', line):
                rpms.add (r)

      if len (rpms) > 0:
        for r in rpms:
          log (f'Deleting {r}...')
          if os.path.isfile (r):
            os.remove (r)
        for l in g_test_cmd_steps:
          # delete log and rotate_log product (nb: keep in sync)
          for ext in ['.log', '.log.bak']:
            lf = os.path.join (log_base, l + ext)
            if os.path.isfile (lf):
              log (f'Deleting {lf}...')
              os.remove (lf)
      else:
        raise Error (f'No RPMs found in {log_base}/*.log files.')

      continue

    log_file = os.path.join (log_base, g_args.STEP + '.log')
    rotate_log (log_file)

    log (f'Creating test RPMs for `{base_arch}` target (logging to {log_file})...')

    rpms = run_log (log_file,
      [RPMBUILD_EXE, f'--target={base_arch}', '--define=dist %nil',
        f'--define=_sourcedir {source_dir}'] + opts + [full_spec],
      rf'^Wrote: +(.+\.(?:{base_arch}|noarch)\.rpm)$')

    # Show the generated RPMs when appropriate.
    if g_args.STEP == 'all' or g_args.STEP == 'pack':
      if len (rpms):
        log ('Successfully generated the following RPMs:')
        log ('\n'.join (rpms))
      else:
        raise Error (f'Cannot find `.({base_arch}|noarch).rpm` file names in `{log_file}`.')


#
# -----------------------------------------------------------------------------
#
# Move command. Also used to implement upload and remove.
#

def move_cmd ():

  is_upload = g_args.COMMAND == 'upload'
  is_remove = g_args.COMMAND == 'remove'
  is_remove_local = is_remove and not g_args.GROUP

  if not is_upload and not is_remove_local:
    # No need in per-spec INI loading, load them from each non-plus spec_dir instead.
    config = copy.deepcopy (g_config)
    for dirs in g_spec_dirs:
      config.read (os.path.join (dirs [0], SCRIPT_INI_FILE))

  for spec in g_args.SPEC.split (','):

    if is_upload or is_remove_local:
      # Will use the last built version (if any).
      config = copy.deepcopy (g_config)
      full_spec, spec_base, spec_aux_dir = resolve_spec (spec, g_spec_dirs, config)
      ver = None
    else:
      # Require the version argument.
      try:
        spec, ver = spec.split (':', 1)
      except ValueError:
        raise Error (f'No version given for `{spec}`', hint = 'Use `list` command to get available versions')
      if not re.match (rf'^{VER_FULL_REGEX}$', ver):
        raise Error (f'Invalid version specification: `{ver}`')
      spec_base = spec # Don't deal with path or ext here.

    if is_remove:
      group, to_repo = g_args.GROUP, None
    else:
      group, to_repo = (g_args.GROUP.split (':', 1) + [None]) [:2]

    from_repo = None

    group_config = read_group_config (group, config)

    if not is_remove_local:

      repos = group_config ['repos']

      if not is_upload:
        # Look for a summary in one of the group's repos.
        for repo in repos:
          from_summary = os.path.join (group_config [f'repo.{repo}'] ['log'], spec_base, ver, 'summary')
          if os.path.isfile (from_summary):
            from_repo = repo
            break
        if not from_repo:
          raise Error (f'No build summary for `{spec_base}` version {ver} in any of `{group}` repositories',
            hint = 'Use `upload` command to upload the packages first.')

      if from_repo and not from_repo in group_config ['repos']:
        raise Error (f'No repository `{from_repo}` listed in configured group `{group}`')

    else:

      from_summary = os.path.join (group_config [f'repo.{from_repo}'] ['log'], spec_base, 'summary')

    prompt = False

    if not to_repo and not is_remove:
      # Auto-detect target repo and cause a prompt.
      prompt = True
      if from_repo:
        i = repos.index (from_repo)
        if i < len (repos) - 1:
          to_repo = repos [i + 1]
        else:
          raise Error (f'Build summary for `{spec_base}` already in `{from_repo}`, last in group `{group}`: {from_summary}',
            hint = 'Specify a target repository explicitly')
      else:
        to_repo = repos [0]

    if not is_remove:
      if not to_repo in repos:
        raise Error (f'No repository `{to_repo}` in configured group `{group}`')
      if from_repo == to_repo:
        raise Error (f'Source and target repository are the same: `{to_repo}`')

    from_repo_config = group_config [f'repo.{from_repo}']
    if not is_remove:
      to_repo_config = group_config [f'repo.{to_repo}']

    log (f'From repository : {from_repo_config ['base']}')
    if not is_remove:
      log (f'To repository   : {to_repo_config ['base']}')

    try:
      ver_full, build_user, build_time, rpms, _ = read_build_summary (spec_base, ver, from_repo, group_config)
    except NoBuildSummary as e:
      raise Error (f'No build summary for `{spec_base}` ({e.summary})',
        hint = 'Use `build` command to build the packages first.')

    if not is_upload and not is_remove_local and ver_full != ver:
      raise Error (f'Requested version {ver} differs from version {ver_full} stored in summary')

    log (f'Version         : {ver_full}')
    log (f'Build user      : {build_user}')
    log (f'Build time      : {to_localtimestr (build_time)}')

    if is_remove:
      answer = log_input_warn ('Do you really want to remove this package instead of %s?\n'
        'This operation cannot be undone. Proceed?' %
        ('uploading it' if is_remove_local else 'moving it to an archive repo'), 'YN')
      if not answer == 'Y':
        raise CommandCancelled ()
    elif prompt:
      answer = log_input_warn (f'Target repository `{to_repo}` was auto-detected. Proceed?', 'YN')
      if not answer == 'Y':
        raise CommandCancelled ()

    old_repo = None
    old_summary = None

    if is_remove:
      old_repo = from_repo
      old_summary = from_summary
    else:
      to_summary = os.path.join (to_repo_config ['log'], spec_base, ver_full, 'summary')
      if os.path.isfile (to_summary):
        if g_args.force_command:
          log_note (f'Overwriting previous build of `{spec_base}` due to -f option.')
          old_repo = to_repo
          old_summary = to_summary
        else:
          raise Error (f'Build summary for `{spec_base}` already exists: {to_summary}',
                       hint = 'If recovering from a failure, use -f option to overwrite this build with a new one.')
      elif is_upload:
        # Search for a summary in any group's repo.
        for repo in repos:
          maybe_summary = os.path.join (group_config [f'repo.{repo}'] ['log'], spec_base, ver_full, 'summary')
          if os.path.isfile (maybe_summary):
            if g_args.force_command:
              log_note (f'Ignoring existing build of `{spec_base}` in repository `{repo}` due to -f option.')
              old_repo = repo
              old_summary = maybe_summary
            else:
              raise Error (f'Build summary for `{spec_base}` already exists in `{repo}`: {maybe_summary}',
                hint = 'If recovering from a failure, use -f option to ignore this build.')

    # Attempt to clean up files from the old summary (or just remove stuff for is_remove).
    if old_repo or is_remove_local:
      if not is_remove:
        log ('Removing old build'f's packages and logs for `{old_summary}`...')
      _, _, _, old_rpms, _ = read_build_summary (spec_base, None if is_remove_local else ver_full, old_repo, group_config)
      for arch, files in old_rpms.items ():
        if arch in ['srpm', 'zip']:
          if is_remove:
            log (f'Removing {files}...')
          os.remove (files)
        else:
          for f in files:
            if is_remove:
              log (f'Removing {f}...')
            os.remove (f)
      if is_remove:
        log (f'Removing logs in {os.path.dirname (old_summary)}...')
      remove_path (os.path.dirname (old_summary))

    # Commit the spec file and dir.
    if is_upload:

      commit_msg = f'{spec_base}: Release version {ver_full}.'

      vcs = get_vcs_type (full_spec).upper ()
      log (f'\nThe spec file is under {vcs} version control and needs to be committed as:')
      log (f'  "{commit_msg}"\n\n')
      log ('The working copy will be updated now and then you will get a diff for careful '
        'inspection. If this process (or a subsequent commit) fails, you will have to '
        'fix the failure manually and then re-run the `upload` command again.', wrap_width = 0)
      if not log_input ('Press Enter to continue.') == '':
        raise CommandCancelled ()

      log ('')
      spec_dir = os.path.dirname (full_spec)
      spec_file = os.path.basename (full_spec)

      if vcs == 'GIT':
        # Check for untracked files in spec AUX dir.
        untracked = command_output (['git', 'ls-files', '--other', '--', '.'], cwd = spec_aux_dir)
        if untracked.strip () != '':
          raise Error (f'Untracked files are found in `{spec_aux_dir}`:\n{'\n'.join (f'  {f}' for f in untracked.splitlines ())}\n\n',
            hint = 'Add these files with `git add` (or remove/ignore them) manually and retry.')
        # Check for modified files.
        commit_files = ['.'] if spec_dir == spec_aux_dir else [spec_file, spec_aux_dir]
        modified = [os.path.basename (f) for f in command_output (['git', 'diff', '--cached', '--name-only', '--'] + commit_files, cwd = spec_dir).splitlines ()]
        modified += [os.path.basename (f) for f in command_output (['git', 'diff', '--name-only', '--'] + commit_files, cwd = spec_dir).splitlines ()]
        if not spec_file in modified:
          last_spec_msg = command_output (['git', 'log', '-n', '1', '--pretty=format:%s', '--', spec_file], cwd = spec_dir).strip ()
          if last_spec_msg != commit_msg:
            raise Error (f'`{spec_file}` is not modified and has a different last commit message:\n  "{last_spec_msg}"')
        if len (modified) > 0:
          # Show diffs.
          command (['git', 'diff', '--cached', '--'] + commit_files, cwd = spec_dir)
          command (['git', 'diff', '--'] + commit_files, cwd = spec_dir)
          # Confirm diffs.
          answer = log_input ('Type YES if the diff is okay to be committed.')
          if answer != 'YES':
            raise CommandCancelled ()
          # Add changes and commit.
          command (['git', 'pull', '--no-rebase', '--ff-only'], cwd = spec_dir)
          command (['git', 'add', '--'] + commit_files, cwd = spec_dir)
          command (['git', 'commit', '-m', commit_msg, '--'] + commit_files, cwd = spec_dir)
        else:
          log (f'No modified files but the last commit message of `{spec_file}` matches the above.')
        # Finally, push the commit.
        answer = log_input (f'Push the commit to {vcs} and upload RPMs to `{to_repo}`?', 'YN')
        if not answer == 'Y':
          raise CommandCancelled ()
        command (['git', 'push'], cwd = spec_dir)
      else:
        raise Error (f'Unsupported version control system: {vcs}')


    # Copy RPMs.
    if not is_remove:

      # Check that the base dir exists just in case (note that we don't want to implicitly create it here).
      if not os.path.isdir (group_config ['base']):
        raise Error (group_config ['base'], 'Not a directory')

      rpms_to_copy = []

      for arch, files in rpms.items ():
        if arch in ['srpm', 'zip']:
          dst = to_repo_config [arch]
          rpms_to_copy.append ((files, dst))
        else:
          dst = os.path.join (to_repo_config ['rpm'], arch)
          for src in files:
            rpms_to_copy.append ((src, dst))

      for src, dst in rpms_to_copy:
        log (f'Copying {src} -> {dst}...')
        ensure_dir (dst)
        shutil.copy2 (src, dst)

      # Copy build logs and summary.

      from_log = os.path.join (from_repo_config ['log'], spec_base)
      if not is_upload:
        from_log = os.path.join (from_log, ver_full)

      zip_path = os.path.join (from_log, 'logs.zip')

      if is_upload:
        # Local build - zip all logs (otherwise they are already zipped).
        log (f'Packing logs to {zip_path}...')
        zip_files = []
        for arch in rpms:
          if arch != 'noarch':
            zip_files.append (os.path.join (from_log, f'{arch}.log'))
          else:
            base_arch = get_basespec_arch (config, spec_base)
            zip_files.append (os.path.join (from_log, f'{base_arch}.log'))
        run_pipe ([['zip', '-jy9', zip_path] + zip_files])

      to_log = os.path.join (to_repo_config ['log'], spec_base, ver_full)

      log (f'Copying logs {from_log} -> {to_log}...')

      remove_path (to_log)
      ensure_dir (to_log)

      logs_to_copy = [zip_path, os.path.join (from_log, 'summary')]
      for src in logs_to_copy:
        shutil.copy2 (src, to_log)

      # Record the transition.
      with open (to_summary, 'a') as f:
        f.write (f'>{to_repo}|{g_username}@{g_hostname}|{time.time ()}\n')

      log ('Removing copied packages...')

      for src, _ in rpms_to_copy:
        os.remove (src)

      if not is_upload:

        # Clean up remote repository.
        log ('Removing copied logs...')
        for src in logs_to_copy:
          os.remove (src)

      else:

        # Archive local logs.
        archive_dir = os.path.join (g_log_dir, 'archive', spec_base, ver_full)
        log (f'Archiving logs to {archive_dir}...')
        remove_path (archive_dir)
        ensure_dir (archive_dir)
        for src in logs_to_copy:
          shutil.move (src, archive_dir)

        # Remove unpacked logs.
        for src in zip_files:
          if os.path.exists (src):
            os.remove (src)

      # Remove source log dir.
      remove_path (from_log)

    # Remove the base spec's dir (only if it's empty).
    if not is_upload and not is_remove_local:
      if is_remove: # from_log is not set in if_remove
        from_log = os.path.dirname (from_summary)
      from_log_base = os.path.dirname (from_log)
      try:
        # On some IFSes os.rmdir will delete the directory even if it's not
        # empty (e.g. on NDFS/WebDAV, see #5). This would eventually kill logs
        # of other package's versions and break many bot's commands. Protect from that.
        if len (os.listdir (from_log_base)) == 0:
          os.rmdir (from_log_base)
      except OSError as e:
        if e.errno != 2:
          raise


#
# -----------------------------------------------------------------------------
#
# List command.
#

def list_cmd ():

  # No need in per-spec INI loading, load them from each non-plus spec_dir instead.
  config = copy.deepcopy (g_config)
  for dirs in g_spec_dirs:
    config.read (os.path.join (dirs [0], SCRIPT_INI_FILE))

  group_mask, repo_mask = (g_args.GROUP.split (':', 1) + ['*']) [:2]

  for section in config.sections ():

    if fnmatch.fnmatch (section, f'group.{group_mask}'):

      _, group = section.split ('.')
      group_config = read_group_config (group, config)
      repos = group_config ['repos']

      for repo in repos:

        if fnmatch.fnmatch (repo, repo_mask):

          log_base = os.path.join (group_config [f'repo.{repo}'] ['log'])

          # Ignore missing log dirs.
          files = []
          try:
            files = os.listdir (log_base)
          except OSError as e:
            if e.errno == 2:
              pass

          for spec in files:

            log_dir = os.path.join (log_base, spec)

            if os.path.isdir (log_dir):
              for spec_mask in g_args.SPEC.split (','):
                if fnmatch.fnmatch (spec, spec_mask):
                  for ver in os.listdir (log_dir):
                    if os.path.isdir (os.path.join (log_dir, ver)):

                      # NOTE: Don't call #read_build_summary to save time.
                      log (f'{f'{group}:{repo}':<20} {spec}:{ver}')


#
# -----------------------------------------------------------------------------
#
# Info command.
#

def info_cmd ():

  # No need in per-spec INI loading, load them from each non-plus spec_dir instead.
  config = copy.deepcopy (g_config)
  for dirs in g_spec_dirs:
    config.read (os.path.join (dirs [0], SCRIPT_INI_FILE))

  try:
    group, repo = g_args.GROUP.split (':', 1)
  except ValueError:
    raise Error (f'No repository given after `{g_args.GROUP}`')

  for spec in g_args.SPEC.split (','):

    try:
      spec, ver = spec.split (':', 1)
    except ValueError:
      raise Error (f'No version given for `{spec}`', hint = 'Use `list` command to get available versions')
    if not re.match (rf'^{VER_FULL_REGEX}$', ver):
      raise Error (f'Invalid version specification: `{ver}`')

    group_config = read_group_config (group, config)
    repo_config = group_config [f'repo.{repo}']

    try:
      ver_full, build_user, build_time, rpms, hist = read_build_summary (spec, ver, repo, group_config)
    except NoBuildSummary as e:
      raise Error (f'No build summary for `{spec}` version {ver} ({e.summary})')

    log (f'Rrepository  : {repo_config ['base']}')
    log (f'Version      : {ver_full}')
    log (f'Build user   : {build_user}')
    log (f'Build time   : {to_localtimestr (build_time)}')

    if True:
      first = True
      for arch, files in rpms.items ():
        str = 'RPMs         : %s' if first else '             : %s'
        first = False
        if arch in ['srpm', 'zip']:
          log (str % os.path.basename (files))
        else:
          for r in files:
            log (str % os.path.basename (r))
    else:
      log ('RPMs         :')
      for arch, files in rpms.items ():
        str = '  %s'
        if arch in ['srpm', 'zip']:
          log (str % os.path.basename (files))
        else:
          for r in files:
            log (str % os.path.basename (r))

    if True:
      first = True
      for h in hist:
        str = 'Move history :' if first else '             :'
        first = False
        log (f'{str} -> {h [0]} by {h [1]} on {to_localtimestr(h [2])}')
    else:
      log ('Move history :')
      for h in hist:
        log (f'  -> {h [0]} by {h [1]} on {to_localtimestr(h [2])}')

#
# =============================================================================
#
# Main
#

# Fix slashes in common environment vars to ensure backslashes won't slip in
# (which is generally bad because of escaping hell when passing them around).
for v in ['HOME', 'TMP', 'TEMP', 'TMPDIR', 'PATH']:
  os.environ [v] = os.environ [v].replace ('\\', '/')

# Script's start timestamp.
g_start_ts = datetime.datetime.now ().astimezone ()

# Script's own log file.
g_log = None

# Cache of RPM macro values.
g_rpm = {}

# RPM macros to pre-evaluate.
g_rpmbuild_used_macros = ['_topdir', '_sourcedir', 'dist', '_bindir', '_rpmdir', '_srcrpmdir']

# Script's output redirection (for #func_log).
g_output_file = None

# Environment used for all external commands.
g_run_env = None

# Parse command line.

g_cmdline = argparse.ArgumentParser (formatter_class = argparse.ArgumentDefaultsHelpFormatter, description = '''
A frontend to rpmbuild that provides a centralized way to build RPM packages from
RPM spec files and move them later across configured repositories.''', epilog = '''
Specify COMMAND -h to get help on a particular command.''')

g_cmdline.add_argument ('--version', action = 'version', version = '%(prog)s ' + VERSION)
g_cmdline.add_argument ('-l', action = 'store_true', dest = 'log_to_console', help = 'echo log output to console')
g_cmdline.add_argument ('-f', action = 'store_true', dest = 'force_command', help = 'force command execution')

g_cmds = g_cmdline.add_subparsers (dest = 'COMMAND', metavar = 'COMMAND', help = 'command to run:')

# Parse data for test command.

g_cmd_test = g_cmds.add_parser ('test',
  help = 'do test build (one arch)', description = '''
Runs a test build of SPEC for one architecture. STEP may speficty a rpmbuild
shortcut to go to a specific build step. If STEP is `purge`, it will delete all
RPM files and logs generated by a last successfl test build.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_test.add_argument ('STEP', nargs = '?', choices = ['all', 'prep', 'build', 'install', 'pack', 'purge'], default = 'all', help = 'build step: %(choices)s', metavar = 'STEP')
g_cmd_test.add_argument ('SPEC', help = 'spec file (comma-separated if more than one)')
g_cmd_test.set_defaults (cmd = test_cmd)

# Parse data for build command.

g_cmd_build = g_cmds.add_parser ('build',
  help = 'do normal build (all configured archs)', description = '''
Builds SPEC for all configured architectures. If SPEC does not have a path (recommended),
it will be searcherd in configured SPEC directories.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_build.add_argument ('SPEC', help = 'spec file (comma-separated if more than one)')
g_cmd_build.set_defaults (cmd = build_cmd)

# Parse data for upload command.

g_cmd_upload = g_cmds.add_parser ('upload',
  help = 'upload build results to repository group', description = '''
Uploads all RPMs generated from SPEC to a repository of a configured repository group.
If REPO is not specified, the first GROUP's repository is used as a target.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_upload.add_argument ('GROUP', help = 'repository group and optional repository name from INI file', metavar = 'GROUP[:REPO]')
g_cmd_upload.add_argument ('SPEC', help = 'spec file (comma-separated if more than one)')
g_cmd_upload.set_defaults (cmd = move_cmd)

# Parse data for move command.

g_cmd_move = g_cmds.add_parser ('move',
  help = 'move build results to another repository in group', description = '''
Moves all RPMs built from SPEC to a given repository of a configured repository group.
The RPMs must already reside in a different repository of this group (as a result of `upload`
or another `move`). If REPO is not specified, the next GROUP's repository is used as a target.
VER must specify a version of the build to be moved.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_move.add_argument ('GROUP', help = 'repository group and optional repository name from INI file', metavar = 'GROUP[:REPO]')
g_cmd_move.add_argument ('SPEC', help = 'spec name and version (comma-separated if more than one)', metavar = 'SPEC:VER')
g_cmd_move.set_defaults (cmd = move_cmd)

# Parse data for remove command.

g_cmd_remove = g_cmds.add_parser ('remove',
  help = 'remove build results locally or from repository in group', description = '''
Removes all RPMs built from SPEC with `build` command or moved to a repository of a configured repository group
with `upload` or `move` command.
If GROUP is not specified, local results of `build` command will be removed and VER specification is ignored.
Otherwise, SPEC's RPMs will be looked up in repositories of the given group and removed if found;
VER must specify a version of the build to remove in this case.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_remove.add_argument ('GROUP', help = 'optional repository group name from INI file', metavar = 'GROUP', nargs = '?')
g_cmd_remove.add_argument ('SPEC', help = 'spec name and version (comma-separated if more than one)', metavar = 'SPEC[:VER]')
g_cmd_remove.set_defaults (cmd = move_cmd)

# Parse data for list command.

g_cmd_list = g_cmds.add_parser ('list',
  help = 'list build versions available in remote repositories', description = '''
Lists all versions of RPMs built from SPEC in a given repository of a configured repository group.
Wildcard characters *, ? and [] may be used for GROUP, REPO and SPEC to limit the output to specific
repositories and packages. If no arguments are given, all build results from all repositories will be
listed.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_list.add_argument ('GROUP', help = 'repository group and optional repository name wildcards', metavar = 'GROUP[:REPO]', nargs = '?', default = '*')
g_cmd_list.add_argument ('SPEC', help = 'spec name wildcard (comma-separated if more than one)', metavar = 'SPEC', nargs = '?', default = '*')
g_cmd_list.set_defaults (cmd = list_cmd)

# Parse data for info command.

g_cmd_info = g_cmds.add_parser ('info',
  help = 'show build info from remote repository', description = '''
Shows information about RPMs built from SPEC in a given repository of a configured repository group.
REPO must specify a GROUP's repository.
VER must specify a version of the build to be shown.''',
  formatter_class = g_cmdline.formatter_class)

g_cmd_info.add_argument ('GROUP', help = 'repository group and repository name from INI file', metavar = 'GROUP:REPO')
g_cmd_info.add_argument ('SPEC', help = 'spec name (comma-separated if more than one)', metavar = 'SPEC:VER')
g_cmd_info.set_defaults (cmd = info_cmd)

# Finally, do the parsing.

g_args = g_cmdline.parse_args ()

g_main_ini_path = os.path.expanduser (f'~/{SCRIPT_INI_FILE}')

g_config = Config (g_rpm)

# List of lists, SCRIPT_INI_FILE in the 1st dir of each nested list is subject to loading.
g_spec_dirs = []

rc = 255

try:

  # Detect user and hostname.

  g_username = getpass.getuser ()
  if not g_username:
    raise Error ('Cannot determine user name of this build machine.')

  g_hostname = socket.gethostname ()
  if not g_hostname:
    raise Error ('Cannot determine host name of this build machine.')

  # Read the main config file.

  try:
    with open (g_main_ini_path, 'r') as f:
      g_config.read_file (f)
  except OSError as e:
    raise Error (f'Cannot read configuration from `{g_main_ini_path}`:\n{e}')

  for d in g_config.getlines ('general:spec_dirs'):
    if d [0] == '+' and len (g_spec_dirs):
      g_spec_dirs [-1].append (d [1:].lstrip ())
    else:
      g_spec_dirs.append ([d])

  rc = 0

  # Pre-evaluate some RPMBUILD macros (this will also chedk for RPMBUILD_EXE availability).

  for i, m in enumerate (command_output ([
    RPMBUILD_EXE, '--eval',
    ''.join ('|%{?' + s + '}' for s in g_rpmbuild_used_macros).lstrip ('|')
  ]).strip ().split ('|')):
    g_rpm [g_rpmbuild_used_macros [i]] = m

  for m in ['_topdir', '_sourcedir']:
    if not g_rpm [m] or not os.path.isdir (g_rpm [m]):
      raise Error (f'Value of `%{m}` in rpmbuild is `{g_rpm [m]}` and not a directory')

  # Prepare some (non-rpmbuild-standard) directories.

  g_zip_dir = os.path.join (g_rpm ['_topdir'], 'zip')
  g_log_dir = os.path.join (g_rpm ['_topdir'], 'logs')

  for d in [g_zip_dir] + [os.path.join (g_log_dir, f) for f in ('test', 'build')]:
    ensure_dir (d)

  # Create own log file unless redirected to a file.

  if sys.stdout.isatty ():
    d = os.path.join (g_log_dir, SCRIPT_LOG_FILE)
    rotate_log (d)
    g_log = open (d, 'w', buffering = 1)  # noqa: SIM115
    sys.stdout.write (f'[Logging to {d}]\n')
  else:
    g_log = sys.stdout
    sys.stdout.write ('[Logging to <console, redirected>]\n')

  g_log.write (f'[{g_start_ts.strftime (DATETIME_FMT)}, {' '.join (sys.argv)}]\n')

  # Run command.

  g_args.cmd ()

except (configparser.NoSectionError, configparser.NoOptionError, configparser.InterpolationError) as e:

  log_err ('config', str (e))
  log_hint (f'Check `{g_main_ini_path}` or spec-specific INI files')
  rc = 1

except OSError as e:
  log_err (str (e))
  rc = 2

except RunError as e:

  msg = f'The following command failed with: {e.msg}:\n  {e.cmd}'
  if not e.hint and e.log_file:
    e.hint = f'Inspect `{e.log_file}` for more info.'
  log_err (e.prefix, msg)
  if e.hint:
    log_hint (e.hint)
  rc = e.code

except Error as e:

  log_err (e.prefix, e.msg)
  if e.prefix == 'config' and not e.hint:
    e.hint = f'Check `{g_main_ini_path}` or spec-specific INI files'
  if e.hint:
    log_hint (e.hint)
  rc = e.code

except CommandCancelled:

  rc = 126

except:  # noqa: E722 (we log below)

  log_err ('Unexpected exception occured:')
  log (traceback.format_exc ())
  rc = 127

finally:

  end_ts = datetime.datetime.now ().astimezone ()
  elapsed = str (end_ts - g_start_ts).rstrip ('0')

  if g_log != sys.stdout:
    sys.stdout.write (f'{not rc and 'Succeeded' or rc == 126 and 'Cancelled' or f'Failed with exit code {rc}'} ({elapsed} s).\n')

  # Finalize own log file.
  if g_log:
    g_log.write (f'[{end_ts.strftime (DATETIME_FMT)}, exit code {rc}, {elapsed} s]\n\n')
    g_log.close ()

sys.exit (rc)
