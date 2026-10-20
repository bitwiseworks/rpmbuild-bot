"""Runs some rpmbuild-bot tests without starting the bot or RPM/Yum."""

import ast
import contextlib
import datetime
import io
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load_bootstrap ():

  path = Path (__file__).with_name ('rpmbuild-bot.py')
  tree = ast.parse (path.read_text (), filename = str (path))
  names =\
  {
    'Error',
    'RunError',
    'collect_build_requires',
    'bootstrap_build_requires',
    'run_pipe',
    'run',
    'run_pipe_log',
    'run_log',
  }
  tree.body = [node for node in tree.body
    if (isinstance (node, (ast.FunctionDef, ast.ClassDef)) and node.name in names)
    or (isinstance (node, ast.Assign) and any (
    isinstance (target, ast.Name) and target.id == 'g_unknown_tag_re'
    for target in node.targets))]
  module = types.ModuleType ('bootstrap')
  module.__dict__.update (os = os, re = re, sys = sys, subprocess = subprocess, datetime = datetime,
    g_run_env = None, g_rpm_macros_support_dash = True,
    g_args = types.SimpleNamespace (log_to_console = False),
    g_output_file = None, g_log = io.StringIO (),
    DATETIME_FMT = '%Y-%m-%d %H:%M:%S%z',
    RPMSPEC_EXE = 'rpmspec.exe', YUM_EXE = 'yum.exe',
    log = Mock (), log_warn = Mock ())
  exec (compile (tree, str (path), 'exec'), module.__dict__)  # noqa: S102
  return module


class BootstrapTests (unittest.TestCase):

  @staticmethod
  def result (code = 0, out = '', err = ''):

    return subprocess.CompletedProcess ([], code, out, err)

  def setUp (self):

    self.bot = load_bootstrap ()
    self.bot.run = Mock (return_value = [])
    self.query = Mock (return_value = self.result ())
    runner = patch.object (subprocess, 'run', lambda args, **kwargs: self.query (args, **kwargs))
    runner.start ()
    self.addCleanup (runner.stop)
    self.options = ['--target=i686', '--define=_sourcedir /sources', '--define=dist %nil']

  def test_requirements_keep_versions_and_filter_internal_features (self):

    self.query.return_value = self.result (out = 'gcc >= 12\nfoo\nfoo\nrpmlib(Test) <= 1\n')
    deps, faked = self.bot.collect_build_requires ('test.spec', self.options)
    self.assertEqual (deps, {'gcc >= 12', 'foo'})
    self.assertEqual (faked, set ())
    args = self.query.call_args.args [0]
    self.assertEqual (args, ['rpmspec.exe', '-q', '--buildrequires', *self.options, 'test.spec'])

  def test_empty_requirements_do_not_invoke_yum (self):

    deps, faked = self.bot.collect_build_requires ('test.spec', [])
    self.assertEqual ((deps, faked), (set (), set ()))
    self.bot.bootstrap_build_requires ('test.spec', [])
    self.bot.run.assert_not_called ()

  def test_unrecoverable_parse_error_is_left_for_the_real_build (self):

    for err in ['error: bad date in %changelog\n',
      'error: Unknown tag: ordinary text\n',
      'warning: Unknown tag: %not_an_error arg\n']:
      with self.subTest (err = err):
        self.query = Mock (return_value = self.result (1, err = err))
        self.assertEqual (self.bot.collect_build_requires ('test.spec', []), (None, set ()))
        self.assertEqual (self.query.call_count, 1)
    self.bot.bootstrap_build_requires ('test.spec', [])
    self.bot.run.assert_not_called ()
    self.bot.log_warn.assert_called_once ()

  def test_multiple_missing_macros_are_stubbed_without_eval_probes (self):

    calls = []
    def query (args, **kwargs):

      calls.append (args)
      self.assertNotIn ('--eval', args)
      if not any (a.startswith ('--define=first(') for a in args):
        return self.result (1, err = 'error: line 5: Unknown tag: %first -n pkg /usr/bin/a\n')
      if not any (a.startswith ('--define=second(') for a in args):
        return self.result (1, err = 'error: line 9: Unknown tag: %{second} /usr/bin/b\n')
      return self.result (out = 'gcc\n')
    self.query.side_effect = query
    deps, faked = self.bot.collect_build_requires ('test.spec', self.options)
    self.assertEqual (faked, {'first', 'second'})
    self.assertEqual (deps, {'gcc', 'rpm_macro(first)', 'rpm_macro(second)'})
    self.assertEqual (len (calls), 3)
    self.assertIn ('--define=first(-) %{nil}', calls [-1])
    self.assertIn ('--define=second(-) %{nil}', calls [-1])

  def test_legacy_rpm_declares_short_options (self):

    self.bot.g_rpm_macros_support_dash = False
    def query (args, **kwargs):

      if any (a.startswith ('--define=first(') for a in args):
        definition = next (a for a in args if a.startswith ('--define=first('))
        self.assertIn ('(abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789)', definition)
        return self.result (out = 'gcc\n')
      return self.result (1, err = 'error: Unknown tag: %first -n pkg -v 1-1\n')
    self.query.side_effect = query
    self.assertIn ('rpm_macro(first)', self.bot.collect_build_requires ('test.spec', []) [0])

  def test_matching_errors_are_collected_even_with_other_diagnostics (self):

    self.query.side_effect =\
    [
      self.result (1, err = 'warning: other diagnostic\nerror: unrelated error\nerror: Unknown tag: %first arg\n'),
      self.result (out = 'gcc\n'),
    ]
    self.assertEqual (self.bot.collect_build_requires ('test.spec', []),
      ({'gcc', 'rpm_macro(first)'}, {'first'}))

  def test_repeated_unknown_tag_returns_partial_failure (self):

    self.query.return_value = self.result (1, err = 'error: Unknown tag: %first arg\n')
    self.assertEqual (self.bot.collect_build_requires ('test.spec', []), (None, {'first'}))
    self.assertEqual (self.query.call_count, 2)

  def test_missing_macro_retry_limit (self):

    def query (args, **kwargs):

      count = sum (a.startswith ('--define=') for a in args)
      return self.result (1, err = f'error: Unknown tag: %macro{count} arg\n')
    self.query.side_effect = query
    with self.assertRaisesRegex (self.bot.Error, 'Too many missing-macro retries \\(64\\)'):
      self.bot.collect_build_requires ('test.spec', [])
    self.assertEqual (self.query.call_count, 64)

  def test_unexpanded_requirements_abort (self):

    self.query.return_value = self.result (out = '%{missing}-devel\n')
    with self.assertRaisesRegex (self.bot.Error, 'Unexpanded dependency'):
      self.bot.collect_build_requires ('test.spec', [])

  def test_query_uses_the_configured_environment (self):

    self.bot.g_run_env = {'PATH': '/tools', 'LC_ALL': 'C'}
    self.bot.collect_build_requires ('test.spec', [])
    kwargs = self.query.call_args.kwargs
    self.assertIs (kwargs ['env'], self.bot.g_run_env)
    self.assertTrue (kwargs ['capture_output'])
    self.assertFalse (kwargs ['check'])

  def test_query_os_error_is_reported (self):

    self.query.side_effect = OSError (2, 'not found')
    with self.assertRaisesRegex (self.bot.RunError, 'OS Error 2 not found'):
      self.bot.collect_build_requires ('test.spec', [])

  def test_requeries_without_fakes_and_installs_new_real_requirements (self):

    self.bot.collect_build_requires = Mock (side_effect = [
      ({'gcc', 'rpm_macro(first)'}, {'first'}),
      ({'gcc', 'runtime-devel >= 2'}, set ()),
      ({'gcc', 'runtime-devel >= 2'}, set ())])
    self.bot.bootstrap_build_requires ('test.spec', self.options)
    first, second = self.bot.run.call_args_list
    self.assertEqual (first.args [0], ['yum.exe', '-y', 'install', 'gcc', 'rpm_macro(first)'])
    self.assertEqual (second.args [0], ['yum.exe', '-y', 'install', 'runtime-devel >= 2'])
    self.assertIs (first.args [2], self.bot.g_log)
    self.assertEqual (re.findall (first.args [1], 'No package gcc available.'), ['gcc'])
    self.assertEqual (first.kwargs ['output_regex'] [0], None)
    self.assertEqual (self.bot.collect_build_requires.call_count, 3)

  def test_missing_provider_cannot_loop_or_succeed (self):

    self.bot.collect_build_requires = Mock (return_value = ({'rpm_macro(first)'}, {'first'}))
    self.bot.run.return_value = ['rpm_macro(first)']
    with self.assertRaisesRegex (self.bot.Error, 'Undefined macros: %first'):
      self.bot.bootstrap_build_requires ('test.spec', [])
    self.assertEqual (self.bot.run.call_count, 1)

  def test_unavailable_ordinary_dependency_aborts_even_if_yum_succeeds (self):

    self.bot.collect_build_requires = Mock (return_value = ({'gcc', 'rpm_macro(first)'}, {'first'}))
    self.bot.run.return_value = ['gcc', 'rpm_macro(first)']
    with self.assertRaisesRegex (self.bot.Error, '1 unavailable package'):
      self.bot.bootstrap_build_requires ('test.spec', [])
    self.assertEqual (self.bot.collect_build_requires.call_count, 1)

  def test_yum_failure_and_captured_lines_are_propagated (self):

    self.bot.collect_build_requires = Mock (return_value = ({'gcc'}, set ()))
    failure = self.bot.RunError ('yum', 'failed', lines = ['gcc'])
    self.bot.run.side_effect = failure
    with self.assertRaises (self.bot.RunError) as caught:
      self.bot.bootstrap_build_requires ('test.spec', [])
    self.assertIs (caught.exception, failure)
    self.assertEqual (caught.exception.lines, ['gcc'])

  def test_installation_round_limit (self):

    counter = iter (range (100))
    self.bot.collect_build_requires = lambda *args: ({f'dep{next (counter)}'}, set ())
    with self.assertRaisesRegex (self.bot.Error, 'Too many dependency installation attempts'):
      self.bot.bootstrap_build_requires ('test.spec', [])
    self.assertEqual (self.bot.run.call_count, 64)


class OutputFilterTests (unittest.TestCase):

  def setUp (self):

    self.bot = load_bootstrap ()
    self.args = [sys.executable, '-c', 'print("hide noise"); print("keep detail")']

  def test_visible_lines_are_filtered_but_the_log_is_complete (self):

    console, logfile = io.StringIO (), io.StringIO ()
    with contextlib.redirect_stdout (console):
      lines = self.bot.run (self.args, r'^(keep) (.+)$', logfile,
        output_regex = (None, r'^hide'))
    self.assertEqual (lines, [('keep', 'detail')])
    self.assertEqual (console.getvalue (), 'keep detail\n')
    self.assertEqual (logfile.getvalue (), 'hide noise\nkeep detail\n')

  def test_exclusion_only_filter_enables_capture_without_collection_regex (self):

    console = io.StringIO ()
    with tempfile.TemporaryFile (mode = 'w+') as logfile:
      with contextlib.redirect_stdout (console):
        self.bot.run (self.args, file = logfile, output_regex = (None, r'^hide'))
      logfile.seek (0)
      self.assertEqual (logfile.read (), 'hide noise\nkeep detail\n')
    self.assertEqual (console.getvalue (), 'keep detail\n')

  def test_stdout_log_preserves_all_lines_without_duplication (self):

    console = io.StringIO ()
    with contextlib.redirect_stdout (console):
      self.bot.run (self.args, r'^keep (.+)$', console,
        output_regex = (None, r'^hide'))
    self.assertEqual (console.getvalue (), 'hide noise\nkeep detail\n')

  def test_failed_command_preserves_captured_matches (self):

    args = [sys.executable, '-c', 'print("No package missing available."); raise SystemExit(1)']
    with self.assertRaises (self.bot.RunError) as caught:
      self.bot.run (args, r'^No package (.+) available[.]$')
    self.assertEqual (caught.exception.lines, ['missing'])

  def test_log_wrapper_preserves_matches_on_failure (self):

    args = [sys.executable, '-c', 'print("No package missing available."); raise SystemExit(1)']
    with tempfile.TemporaryDirectory () as directory:
      path = str (Path (directory) / 'yum.log')
      with self.assertRaises (self.bot.RunError) as caught:
        self.bot.run_log (path, args, r'^No package (.+) available[.]$')
      self.assertEqual (caught.exception.lines, ['missing'])
      self.assertEqual (caught.exception.log_file, path)
      self.assertIn ('No package missing available.', Path (path).read_text ())


class PipelineTests (unittest.TestCase):

  def setUp (self):

    self.bot = load_bootstrap ()
    self.copy = [sys.executable, '-c', 'import sys; sys.stdout.write(sys.stdin.read())']

  def tracked_pipeline (self, commands, fail_at = None, **kwargs):

    processes, streams = [], []
    popen, fdopen = subprocess.Popen, os.fdopen
    launches = 0

    def spawn (*args, **options):

      nonlocal launches
      launches += 1
      if launches == fail_at:
        raise OSError (2, 'pipeline command not found')
      process = popen (*args, **options)
      processes.append (process)
      return process

    def open_pipe (*args, **options):

      stream = fdopen (*args, **options)
      streams.append (stream)
      return stream

    with patch.object (subprocess, 'Popen', side_effect = spawn), \
      patch.object (os, 'fdopen', side_effect = open_pipe):
      try:
        return self.bot.run_pipe (commands, **kwargs)
      finally:
        for process in processes:
          self.assertIsNotNone (process.returncode, 'child was not waited for')
          if process.stdout is not None:
            self.assertTrue (process.stdout.closed, 'parent pipe was not closed')
        for stream in streams:
          self.assertTrue (stream.closed, 'capture pipe was not closed')

  def test_two_stage_pipeline_transforms_and_captures_output (self):

    producer = [sys.executable, '-c', 'print("hello")']
    upper = [sys.executable, '-c', 'import sys; sys.stdout.write(sys.stdin.read().upper())']
    self.assertEqual (self.tracked_pipeline ([producer, upper], regex = r'^(HELLO)$'), ['HELLO'])

  def test_equal_and_reused_commands_are_distinguished_by_position (self):

    producer = [sys.executable, '-c', 'print("payload")']
    for final in (list (self.copy), self.copy):
      with self.subTest (reused = final is self.copy):
        self.assertEqual (self.tracked_pipeline ([producer, self.copy, final],
          regex = r'^(payload)$'), ['payload'])

  def test_pipeline_can_write_directly_to_log_without_capture (self):

    producer = [sys.executable, '-c', 'print("payload")']
    with tempfile.TemporaryFile (mode = 'w+') as logfile:
      self.assertEqual (self.tracked_pipeline ([producer, self.copy], file = logfile), [])
      self.assertFalse (logfile.closed)
      logfile.seek (0)
      self.assertEqual (logfile.read (), 'payload\n')

  def test_stderr_from_every_stage_is_captured (self):

    producer =\
    [
      sys.executable,
      '-c',
      'import sys; print("producer error", file=sys.stderr); print("payload")',
    ]
    middle =\
    [
      sys.executable,
      '-c',
      'import sys; print("middle error", file=sys.stderr); sys.stdout.write(sys.stdin.read())',
    ]
    final =\
    [
      sys.executable,
      '-c',
      'import sys; print("final error", file=sys.stderr); sys.stdout.write(sys.stdin.read())',
    ]
    lines = self.tracked_pipeline ([producer, middle, final], regex = r'^(.+)$')
    self.assertCountEqual (lines, ['producer error', 'middle error', 'final error', 'payload'])

  def test_final_failure_preserves_matches_and_reaps_all_children (self):

    producer = [sys.executable, '-c', 'print("payload")']
    final =\
    [
      sys.executable,
      '-c',
      'import sys; sys.stdout.write(sys.stdin.read()); raise SystemExit(7)',
    ]
    with self.assertRaises (self.bot.RunError) as caught:
      self.tracked_pipeline ([producer, self.copy, final], regex = r'^(payload)$')
    self.assertEqual (caught.exception.lines, ['payload'])
    self.assertIn ('exit code 7', caught.exception.msg)

  def test_only_final_return_code_determines_success (self):

    producer = [sys.executable, '-c', 'print("payload"); raise SystemExit(5)']
    self.assertEqual (self.tracked_pipeline ([producer, self.copy], regex = r'^(payload)$'), ['payload'])

  def test_initial_startup_failure_closes_capture_pipe (self):

    with self.assertRaisesRegex (self.bot.RunError, 'OS Error 2'):
      self.tracked_pipeline ([self.copy, self.copy], fail_at = 1)

  def test_later_startup_failure_closes_pipes_and_reaps_started_children (self):

    # The producer exits without output, so it cannot block on an unread pipe.
    producer = [sys.executable, '-c', 'pass']
    with self.assertRaisesRegex (self.bot.RunError, 'OS Error 2'):
      self.tracked_pipeline ([producer, self.copy], fail_at = 2)

  def test_filtering_preserves_full_log_for_a_pipeline (self):

    producer = [sys.executable, '-c', 'print("hide noise"); print("keep detail")']
    console, logfile = io.StringIO (), io.StringIO ()
    with contextlib.redirect_stdout (console):
      lines = self.tracked_pipeline ([producer, self.copy], regex = r'^keep (.+)$',
        file = logfile, output_regex = (None, r'^hide'))
    self.assertEqual (lines, ['detail'])
    self.assertEqual (console.getvalue (), 'keep detail\n')
    self.assertEqual (logfile.getvalue (), 'hide noise\nkeep detail\n')


if __name__ == '__main__':
  unittest.main ()
