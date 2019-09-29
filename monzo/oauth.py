#!/usr/bin/python2
#
# Usage:
#
#  import sys
#  sys.path.insert(0, "/wherever/this/is")
#  import monzo.oauth
#  monzo.oauth.main()

import json
import httplib
import os
import sys
import time
import urllib

import fetch_base


CLIENT_ID = 'oauth2client_00009mHeBn5bAVvUPAMopF'
REDIRECT = 'https://www.tzone.org/~vandry/monzo/redirect'

def _read_secret():
    with open(os.path.expanduser("~/.monzo_secret")) as f:
        return f.read().strip()
SECRET = _read_secret()


def refresh_token(refresh):
    return call_oauth({
        'grant_type': 'refresh_token',
        'client_id': CLIENT_ID,
        'client_secret': SECRET,
        'refresh_token': refresh,
    })


def get_token(code):
    return call_oauth({
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'client_secret': SECRET,
        'redirect_uri': REDIRECT,
        'code': code,
    })


def call_oauth(params):
    conn = httplib.HTTPSConnection("api.monzo.com", 443)
    headers = {
        "User-Agent": "https://github.com/vandry/banks",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    conn.request("POST", "/oauth2/token", urllib.urlencode(params), headers)
    r = conn.getresponse()
    if r.status != 200:
        raise RuntimeError('%d %s %s' % (r.status, r.reason, r.read()))
    return r.read()


def main():
    if len(sys.argv) != 2:
        raise RuntimeError('Usage: --new or --refresh')
    if sys.argv[1] == '--refresh':
        code = None
    elif sys.argv[1] == '--new':
        code = sys.stdin.read()

    with fetch_base.fetcher_main("~/projects/monzo"):
        if code is None:  # refresh existing code
            with open("../oauthtoken") as f:
                old_token = json.load(f)
            token = refresh_token(old_token['refresh_token'])
        else:
            token = get_token(code)
        tmpfile = "../oauthtoken.new.%d.%d" % (time.time(), os.getpid())
        os.umask(0o077)
        with open(tmpfile, "w") as f:
            f.write(token)
        os.rename(tmpfile, "../oauthtoken")
