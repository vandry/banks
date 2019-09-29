#!/usr/bin/python2
#
# Usage:
#
#  import sys
#  sys.path.insert(0, "/wherever/this/is")
#  import monzo.fetch
#  monzo.fetch.main()

import errno
import json
import os
import re
import subprocess

import fetch_base


_SANITY_RE = re.compile(r'^[0-9a-zA-Z_-]+$')


class MonzoAPI(fetch_base.BankAPI):
    HOSTNAME = "api.monzo.com"

    def __init__(self):
        with open(os.path.expanduser("../oauthtoken")) as f:
            params = json.load(f)
        self.token = params['access_token']


def feed(api, account_id):
    if not _SANITY_RE.match(account_id):
        raise RuntimeError('Bad account')
    try:
        os.mkdir(account_id)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    r = api("/transactions?expand[]=merchant&account_id=" + account_id)
    items = r['transactions']
    for item in items:
        item_id = item['id']
        if not _SANITY_RE.match(item_id):
            raise RuntimeError('Bad feed item UID')
        fn = "%s/%s" % (account_id, item_id)
        with open(fn, "w") as f:
            json.dump(item, f, indent=2, sort_keys=True)
        subprocess.check_call(('git', 'add', fn))


def import_monzo():
    api = MonzoAPI()
    r = api("/accounts")
    accounts = r['accounts']
    for account in accounts:
        id = account['id']
        feed(api, id)


def main():
    with fetch_base.fetcher_main("~/projects/monzo"):
        fetch_base.fetch(import_monzo)
