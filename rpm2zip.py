#!/usr/bin/env python

#
# A quick hack to repack an RPM to zip/7z/whatever.
#
# Also generates a nice list of package requirements that is stored
# as RPM_REQUIREMENTS inside the generated archive.
#
# Author: Dmitriy Kuminov <coding@dmik.org>
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
#

import os, argparse, subprocess, re, tempfile, shutil


RPM_EXE = 'rpm.exe'
RPM2CPIO_EXE = 'rpm2cpio.exe'
CPIO_EXE = 'cpio.exe'

ARCHIVE_TYPE = '7z'
ARCHIVE_CMD = ['7z.exe', 'a', '-r', '%{out}', '*']

#ARCHIVE_TYPE = 'zip'
#ARCHIVE_CMD = ['zip.exe', '-mry9', '%{out}', '.']


def repack_one_file (file):

  print 'Processing `%s`...' % file

  out_file = os.path.splitext (file)
  if out_file [1] == '.rpm':
    out_file = out_file [0] + '.' + ARCHIVE_TYPE
  else:
    out_file = ''.join (out_file) + '.' + ARCHIVE_TYPE

  if os.path.isfile (out_file):
    print 'ERROR: `%s` already exists.' % out_file
    return False

  print 'Generating requirements...'

  reqs = []

  for r in filter (None, subprocess.check_output ([ RPM_EXE, '-q', '--requires', '-p', file ]).split ('\n')):
    s = r.split () [0]
    if not re.match (r'rpmlib\(.+\)', s):
      reqs.append (s)

  if  len (reqs):
    reqs = list (set (filter (None, subprocess.check_output ([ RPM_EXE, '-q', '--whatprovides' ] + reqs).split ('\n'))))
    reqs.sort ()

  cwd = None
  tmpdir = None

  try:

    print 'Unpacking...'

    tmpdir = tempfile.mkdtemp ()

    cwd = os.getcwd ()
    os.chdir (tmpdir)

    subprocess.check_call ('%s %s | %s -idm' % (RPM2CPIO_EXE, file, CPIO_EXE), shell = True)

    with open ('RPM_REQUIREMENTS', 'w') as f:
      f.write ('\n'.join (reqs))

    print 'Packing to `%s`...' % out_file

    cmd = [w.replace ('%{out}', out_file) for w in ARCHIVE_CMD]
    subprocess.check_call (cmd)

  except subprocess.CalledProcessError as e:

    print e.output
    raise

  finally:

    if cwd:
      os.chdir (cwd)

    if tmpdir:
      shutil.rmtree (tmpdir)

  return True

# Main.

g_cmdline = argparse.ArgumentParser (formatter_class = argparse.ArgumentDefaultsHelpFormatter)
g_cmdline.add_argument ('FILE', nargs = '+', help = 'RPM file to repack')
g_args = g_cmdline.parse_args ()

for file in g_args.FILE:
  if not repack_one_file (file):
    break
