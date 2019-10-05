#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Audit the Monzo account's downloaded transaction data."""

import os

import bankrepo


def item_time(item):
    return max(version.payload['created'] for version in item)

def dump_account(feed_items):
    violations = False
    balance = 0
    currencies = set()
    for item in sorted(feed_items.values(), key=item_time):
        print(' ', item)

def main():
    accounts = bankrepo.read_repo(os.path.expanduser('~/monzo/.git'))
    for account_id in sorted(accounts):
        print('Account', account_id)
        dump_account(accounts[account_id][None])

if __name__ == '__main__':
    main()
