#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Audit the Monzo account's downloaded transaction data."""

import os

import bankrepo
import lib


def dump_item(item):
    versionn = item[-1].payload

    created = lib.parse_iso8601(versionn['created'])
    amount = versionn['amount']
    currency = versionn['currency']
    desc = versionn['description']
    print('  %s  %10s  %s' % (
        created.strftime('%Y-%m-%d %H:%MZ'),
        lib.pretty_amount(amount, currency, 'decline_reason' in versionn),
        desc))

def item_time(item):
    return max(version.payload['created'] for version in item)

def dump_account(feed_items):
    violations = False
    balance = 0
    currencies = set()
    for item in sorted(feed_items.values(), key=item_time):
        dump_item(item)
        if 'decline_reason' not in item[-1].payload:
            currencies.add(item[-1].payload['currency'])
            balance += item[-1].payload['amount']
    if len(currencies) > 1:
        print(' Account has multiple currencies!')
    if currencies:
        print(' Balance:', lib.pretty_amount(balance, next(iter(currencies))))


def main():
    accounts = bankrepo.read_repo(os.path.expanduser('~/monzo/.git'))
    for account_id in sorted(accounts):
        print('Account', account_id)
        dump_account(accounts[account_id][None])

if __name__ == '__main__':
    main()
