#!/usr/bin/python3
# -*- coding: utf-8 -*-

import datetime


def parse_iso8601(d):
    return datetime.datetime.strptime(d, "%Y-%m-%dT%H:%M:%S.%fZ")

def pretty_amount(amount, currency, declined=False):
    paren = ('(', ')') if declined else ('', ' ')
    sign = ''
    if amount < 0:
        sign = '-'
        amount *= -1
    if currency == 'GBP':
        ret = sign + '£' + ('%.2f' % (amount / 100.0))
    elif currency == 'EUR':
        ret = sign + '€' + ('%.2f' % (amount / 100.0))
    else:
        ret = '%s%s %s' % (sign, currency, amount)
    return paren[0] + ret + paren[1]
