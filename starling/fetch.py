#!/usr/bin/python2
#
# Usage:
#
#  import sys
#  sys.path.insert(0, "/wherever/this/is")
#  import starling.fetch
#  starling.fetch.main()

import errno
import json
import os
import re
import subprocess

import fetch_base


_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


class StarlingAPI(fetch_base.BankAPI):
    HOSTNAME = "api.starlingbank.com"

    def __init__(self):
        with open(os.path.expanduser("~/.starling_token")) as f:
            self.token = f.read().strip()


def feed(api, account_id, category):
    if not _UUID_RE.match(account_id):
        raise RuntimeError('Bad account')
    if not _UUID_RE.match(category):
        raise RuntimeError('Bad category')
    try:
        os.makedirs("%s/%s" % (account_id, category))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    r = api("/api/v2/feed/account/%s/category/%s?changesSince=%s" % (
        account_id, category, "2019-01-01T00:00:00.000Z"))
    items = r['feedItems']
    for item in items:
        item_uid = item['feedItemUid']
        if not _UUID_RE.match(item_uid):
            raise RuntimeError('Bad feed item UID')
        fn = "%s/%s/%s" % (account_id, category, item_uid)
        with open(fn, "w") as f:
            json.dump(item, f, indent=2, sort_keys=True)
        subprocess.check_call(('git', 'add', fn))


def import_starling():
    api = StarlingAPI()
    r = api("/api/v2/accounts")
    accounts = r['accounts']
    for account in accounts:
        id = account['accountUid']
        category = account['defaultCategory']
        feed(api, id, category)


def main():
    with fetch_base.fetcher_main("~/projects/starling"):
        fetch_base.fetch(import_starling)
