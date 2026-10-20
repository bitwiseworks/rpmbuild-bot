"""Runs some rpmbuild-bot2 tests without starting the bot or RPM/Yum."""

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

  path = Path (__file__).with_name ('rpmbuild-bot2.py')
  tree = ast.parse (path.read_text (), filename = str (path))
  names =\
  {
    'Error',
    'RunError',
    'run_pipe',
    'run',
    'run_pipe_log',
    'run_log',
  }
  tree.body = [node for node in tree.body
    if isinstance (node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
  module = types.ModuleType ('bootstrap')
  module.__dict__.update (os = os, re = re, sys = sys, subprocess = subprocess, datetime = datetime,
    g_run_env = None,
    g_args = types.SimpleNamespace (log_to_console = False),
    g_output_file = None, g_log = io.StringIO (),
    DATETIME_FMT = '%Y-%m-%d %H:%M:%S%z',
    log = Mock ())
  exec (compile (tree, str (path), 'exec'), module.__dict__)  # noqa: S102
  return module


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
