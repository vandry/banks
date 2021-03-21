#!/usr/bin/python

import contextlib
import fcntl
try:
    import http.client
except ImportError:
    import httplib
else:
    httplib = http.client
import json
import os
import subprocess


@contextlib.contextmanager
def fetcher_main(project_dir):
    """cd to <project_dir>/work, grab an exclusive lock

    <project_dir> should contain:
    - A subdirectory called 'work', which is a checked-out git repo
      which we will use as a workspace to prepare a commit, and then
      push that commit.
    - A plain file 'lock', which we will create, to make sure there
      is only one of us in there.
    """
    os.chdir(os.path.join(os.path.expanduser(project_dir), "work"))
    lock = os.open("../lock", os.O_CREAT|os.O_RDWR, 0o666)
    fcntl.lockf(lock, fcntl.LOCK_EX)
    try:
        yield None
    finally:
        os.close(lock)


def fetch(importer):
    """Prepare the workspace, import, then maybe commit the result.

    - Prepare (clean) the workspace
    - Use a bank-specific import function to download transactions
    - If anything was changed in the workspace, commit it.
    """
    subprocess.check_call(('git', 'clean', '-f', '-d', '-q'))

    importer()

    p = subprocess.Popen(('git', 'status', '--porcelain'), stdout=subprocess.PIPE)
    stdout, stderr = p.communicate()
    if p.returncode != 0:
        raise RuntimeError('git status --porcelain failed')
    if not stdout:
        return  # nothing to import

    env = os.environ.copy()
    env['TZ'] = 'Europe/London'
    author = 'Transaction Fetcher <vandry@TZoNE.ORG>'
    p = subprocess.Popen(
        ('git', 'commit', '-m', 'Fetched transactions', '--author', author, '-q'),
        env=env
    )
    status = p.wait()
    if status != 0:
        raise RuntimeError('No commit')

    subprocess.check_call(('git', 'push', '-q', 'origin', 'master'))


class BankAPI(object):
    def __call__(self, url):
        conn = httplib.HTTPSConnection(self.HOSTNAME, 443)
        headers = {
            "Authorization": "Bearer " + self.token,
            "User-Agent": "https://github.com/vandry/banks",
        }
        conn.request("GET", url, headers=headers)
        r = conn.getresponse()
        if r.status == 403 and r.headers['x-2fa-approval-result'] == 'REJECTED':
            r.read()
            twotoken = r.headers['x-2fa-approval']
            headers['X-2FA-Approval'] = twotoken
            headers['X-Signature'] = self.sign_2fa(twotoken)
            conn.request("GET", url, headers=headers)
            r = conn.getresponse()
        if r.status != 200:
            raise RuntimeError('%d %s: %s' % (r.status, r.reason, r.read()))
        return json.loads(r.read().decode('utf-8'))
