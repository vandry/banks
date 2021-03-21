#!/usr/bin/python3
#
# Usage:
#
#  import sys
#  sys.path.insert(0, "/wherever/this/is")
#  import wise.fetch
#  wise.fetch.main()

import base64
import datetime
import errno
import json
import os
import re
import subprocess
import yaml
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5

import fetch_base


class WiseAPI(fetch_base.BankAPI):
    HOSTNAME = "api.transferwise.com"

    def __init__(self):
        with open(os.path.expanduser("~/.wise_api.config.yaml")) as f:
            c = yaml.load(f)
            self.token = c['token']
            self.profile = c['profile']
            key = c['twofa_key']
        self.twofa_key = RSA.importKey(key)

    def sign_2fa(self, message):
        h = SHA256.new(message.encode())
        signer = PKCS1_v1_5.new(self.twofa_key)
        signature = signer.sign(h)
        return base64.b64encode(signature)


def statement(api, account_id, currency):
    try:
        os.mkdir(os.path.join(str(account_id), currency))
    except FileExistsError:
        pass
    if not currency.isalpha():
        raise RuntimeError('Unsafe currency ' + currency)
    now = datetime.datetime.utcnow()
    end = now
    # https://api-docs.transferwise.com/#borderless-accounts-get-account-statement
    # "The period between intervalStart and intervalEnd cannot exceed 469 days"
    # But empirically, we get a 400 if it exceeds ~450 days.
    start = end - datetime.timedelta(days=450)
    r = api("/v3/profiles/%s/borderless-accounts/%s/statement.json?"
            "currency=%s&intervalStart=%sZ&intervalEnd=%sZ&type=COMPACT" % (
                api.profile, account_id, currency, start.isoformat(), end.isoformat()
            )
    )
    for transaction in r['transactions']:
        try:
            # This field makes entries order-dependent and not self-contained.
            del transaction['runningBalance']
        except KeyError:
            pass
        ref = transaction['referenceNumber']
        if ref.startswith('.') or '/' in ref:
            raise RuntimeError('Unsafe referenceNumber ' + ref)
        fn = "%d/%s/%s" % (account_id, currency, ref)
        with open(fn, "w") as f:
            json.dump(transaction, f, indent=2, sort_keys=True)
        subprocess.check_call(('git', 'add', fn))


def import_wise():
    api = WiseAPI()
    r = api("/v1/borderless-accounts?profileId=" + str(api.profile))
    for account in r:
        account_id = int(account['id'])
        try:
            os.mkdir(str(account_id))
        except FileExistsError:
            pass
        for balance in account['balances']:
            statement(api, account_id, balance['currency'])

def main():
    with fetch_base.fetcher_main("~/projects/wise"):
        fetch_base.fetch(import_wise)
