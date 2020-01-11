#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Audit the Starling account's downloaded transaction data."""

import datetime
import os
import sys

import bankrepo
import lib


class MaxValuePolicy(object):
    default = None
    exceptions_by_feedItemUid = {}
    exceptions_by_counterPartyUid = {}
    exceptions_by_commit_id = {}

    @classmethod
    def get_max(cls, transaction):
        payload = transaction.payload
        candidates = [cls.default]
        try:
          c = cls.exceptions_by_feedItemUid[payload['feedItemUid']]
        except KeyError:
          pass
        else:
          candidates.append(c)
        try:
          c = cls.exceptions_by_counterPartyUid[payload['counterPartyUid']]
        except KeyError:
          pass
        else:
          candidates.append(c)
        try:
          c = cls.exceptions_by_commit_id[str(transaction.commit_id)]
        except KeyError:
          pass
        else:
          candidates.append(c)
        return max(candidates)


# Fields in each transaction which are allowed to change upon
# updates and revisions to transactions.
IGNORE_CHANGES = {
    'amount': {
        'minorUnits': None,
    },
    'settlementTime': None,
    'sourceAmount': {
        'minorUnits': None,
    },
    'spendingCategory': None,  # explicly changable in the app UI
    'status': None,
    'updatedAt': None,
}

TIMESTAMP_GRACE_PERIOD = datetime.timedelta(seconds=5)

# Transactions must be seen by us at most this amount of time
# after the transaction's updatedAt timestamp.
class MaxCommitDelay(MaxValuePolicy):
    default = datetime.timedelta(hours=2)
    exceptions_by_commit_id = {
        # This will usually need to include the first commit
        # after some period of time during which the downloaded
        # didn't run for a long time or failed for a long time.
        '6ec91ffd597920d883ded70b30081c5fbfef5803': datetime.timedelta(days=5),
        '9c9e8a05be8c9c9b8cdaf589f607b69f027a5bbf': datetime.timedelta(days=2),
    }

class FirstUpdateDelay(MaxValuePolicy):
    # The first update (updatedAt) of a transaction must be at or very
    # shortly after the time that the transaction claims to have occurred
    # (transactionTime).
    default = datetime.timedelta(minutes=5)
    exceptions_by_counterPartyUid = {
        # This is the Starling account that pays us monthly interest.
        # Due to what is, I guess, a quirk of how that works on their
        # end, and transactionTime is midnight local on the first of
        # the month, but the ttransaction actually appears and is first
        # updatedAt sometime during the following day.
        '45df1294-8bfc-4523-8fac-e5210ce5d72d': datetime.timedelta(days=1),
    }

class LastUpdateDelay(MaxValuePolicy):
    default = datetime.timedelta(days=14)
    exceptions_by_feedItemUid = {
        # Settled after <1d but merchant name changed about 52d later.
        '61de25ad-3df8-4086-9417-125e5d7e5776': datetime.timedelta(days=53),
        # Settled after <1d but merchant name changed about 74d later.
        'e52b322d-67df-4aac-902c-2d0dd37589f3': datetime.timedelta(days=75)
    }
    exceptions_by_commit_id = {
        # A bunch of TfL transactions were inexplicably updated
        # at 2019-11-26T16:25:35.262Z.
        'efb13f2ad9cbee1c3e396d8c49763da87eb9ff32': datetime.timedelta(days=225),
        # A single transaction from 2019-07-03 was inexplicably updated
        # on 2019-12-30 with no other apparent field changes.
        '92981dcdb2dfa44e7f33e6f1468986a39ecc815b': datetime.timedelta(days=181),
    }

class LastUpdateWarningDelay(LastUpdateDelay):
    """Like LastUpdateDelay but less strict; for a warning only"""
    default = datetime.timedelta(days=3)
    exceptions_by_counterPartyUid = {
        # Transport for London. Due to the weekly cap thing, they
        # keep transactions in the pending state for a long time.
        'f45c75f3-7954-454a-beb8-76133a4ca3da': datetime.timedelta(days=13),
    }

STUFF_CHANGED_EXCEPTIONS = {
    # Unexplained change to counterPartyUid and counterPartySubEntityUid
    'adbea8b4-292c-41f1-b737-2d0755323b19',
    # Unexplained change to counterPartySubEntityUid
    'adf7cade-1245-4e17-9c28-781abceb96ba',
    # Unexplained diff to counterPartyUid and counterPartySubEntityUid
    '4ed23f18-1747-494a-a4f9-ee7cdb814d16',
    # Spelling of counterPartyName was corrected (should this just be ignored?)
    'e52b322d-67df-4aac-902c-2d0dd37589f3',
    # Spelling of counterPartyName was corrected (should this just be ignored?)
    '61de25ad-3df8-4086-9417-125e5d7e5776',
    # Unexplained change to counterPartyUid and counterPartySubEntityUid
    '4ed23f18-1747-494a-a4f9-ee7cdb814d16',
    # Unexplained change to counterPartyUid and counterPartySubEntityUid
    '4ed4ba60-c4e6-44e1-b793-06b918225597',
}

WHITELISTED_COMMITS = {
    # On 2020-01-08 sometime around 17:30, a new field "exchangeRate"
    # appeared in the API. This commit happens to contain only the
    # addition of that field. Don't consider it an illegal late revision
    # of the transactions involved.
    '00c08dd9d35b89f95dbbe454fb5507b8e66c4ea4',
    # Transaction status went from REVERSED to SETTLED. Apparently that's
    # a valid state transition, but I won't add it to the rules since it's
    # definitely something suspicious which I would want to vet every time
    # it happens.
    '8050479b15bd8bf3226f19b24c744159a36618a7',
}


def deep_compare(a, b, ignore_parts):
    """Check if a and b are deep equal, ignoring some dict keys.

    a and b are both nested structures of dicts, lists, and basic types.

    ignore_keys should have the same nested structure. Where it has a dict
    key of a given name with value None, the deep comparison of a and b is
    pruned at that dict key. Either a, b, both, or neither, may contain
    that key without changing the result.

    Returns a list of changes
    """
    changes = []
    if hasattr(a, 'items') and hasattr(b, 'items'):
        if ignore_parts is None:
            ignore_parts = {}
        ignore_keys = set(k for k, v in ignore_parts.items() if v is None)
        a_keys = set(a) - ignore_keys
        b_keys = set(b) - ignore_keys
        changes.extend('[%r]' % k for k in a_keys - b_keys)
        changes.extend('[%r]' % k for k in b_keys - a_keys)
        for k in a_keys:
            if k not in b_keys:
                continue
            changes.extend(
                '[%r]%s' % (k, c)
                for c in deep_compare(a[k], b[k], ignore_parts.get(k, None))
            )
    else:
        # TODO: handle lists (not currently needed)
        if a != b:
            changes.append('')
    return changes

def dump_item(item):
    versionn = item[-1].payload

    transaction_time = lib.parse_iso8601(versionn['transactionTime'])
    amount = versionn['amount']
    sign = '-' if versionn['direction'] == 'OUT' else ''
    desc = versionn['counterPartyName']
    print('   %s  %10s  %s' % (
        transaction_time.strftime('%Y-%m-%d %H:%MZ'),
        lib.pretty_amount(
            amount['minorUnits'] * (-1 if versionn['direction'] == 'OUT' else 1),
            amount['currency'], versionn['status'] == 'DECLINED'),
        desc))

    general_violations = []
    general_warnings = []

    update0_time = lib.parse_iso8601(item[0].payload['updatedAt'])
    if update0_time > transaction_time + FirstUpdateDelay.get_max(item[-1]):
        general_violations.append('Transaction first updated too late (%s)' % (update0_time - transaction_time))
    updaten_time = lib.parse_iso8601(versionn['updatedAt'])

    if updaten_time > transaction_time + LastUpdateDelay.get_max(item[-1]):
        general_violations.append('Transaction last updated too late (%s)' % (updaten_time - transaction_time))
    elif updaten_time > transaction_time + LastUpdateWarningDelay.get_max(item[-1]):
        general_warnings.append('Updated quite a long time after the transaction (%s)' % (updaten_time - transaction_time))

    version_violations = []
    prev_payload = None
    old_amounts = []
    old_source_amounts = []
    for version in item:
        if str(version.commit_id) in WHITELISTED_COMMITS:
            prev_payload = version.payload
            continue

        violations = []
        payload = version.payload

        if payload['direction'] not in ('IN', 'OUT'):
            violations.append('unrecognized direction %s' % version['direction'])
        if payload['status'] == 'PENDING':
            if 'settlementTime' in payload:
                violations.append('PENDING transaction has a settlementTime')
        elif payload['status'] == 'SETTLED':
            if 'settlementTime' not in payload:
                violations.append('SETTLED transaction has no settlementTime')
        elif payload['status'] == 'DECLINED':
            if 'settlementTime' in payload:
                violations.append('DECLINED transaction has a settlementTime')
        elif payload['status'] == 'REVERSED':
            if 'settlementTime' in payload:
                violations.append('REVERSED transaction has a settlementTime')
        else:
            violations.append('unrecognized status %s' % versionn['status'])

        update_time = lib.parse_iso8601(payload['updatedAt'])
        if update_time < version.prev_commit_time:
            violations.append('transaction was updated at %s while transactions updated before %s should have been covered in a parent commit' % (
                update_time, version.prev_commit_time))
        if update_time > version.commit_time + TIMESTAMP_GRACE_PERIOD:
            violations.append('transaction with future date: it was updated at %s but committed at %s' % (
                update_time, version.commit_time))
        if (
            update_time < version.commit_time - MaxCommitDelay.get_max(version)
        ):
            violations.append('Took too long (%s) to commit' % (version.commit_time - update_time))

        if transaction_time > update_time + TIMESTAMP_GRACE_PERIOD:
            violations.append('Transaction time %s greater than update time %s' % (transaction_time, update_time))

        if prev_payload is not None:
            if versionn['feedItemUid'] not in STUFF_CHANGED_EXCEPTIONS:
                for c in deep_compare(prev_payload, payload, IGNORE_CHANGES):
                    violations.append('%s changed between versions' % c)
            # This actually happens, apparently legitimately, for unexplained reasons
            # if len(deep_compare(prev_payload, payload, {'updatedAt': None})) == 0:
            #     violations.append('updatedAt changed between versions with no other change')

            if payload['updatedAt'] < prev_payload['updatedAt']:
                violations.append('updatedAt went backwards')

            settled = False
            if prev_payload['status'] == 'PENDING' and payload['status'] == 'SETTLED':
                settled = True
            elif prev_payload['status'] == 'PENDING' and payload['status'] == 'REVERSED':
                pass
            elif prev_payload['status'] != payload['status']:
                violations.append('status went from %s to %s' % (prev_payload['status'], payload['status']))
            if not settled:
                if prev_payload.get('settlementTime', None) != payload.get('settlementTime', None):
                    violations.append('settlementTime changed without the transaction becoming settled')

            if len(deep_compare(prev_payload['amount'], payload['amount'], {})) > 0:
                old_amounts.append(prev_payload['amount'])
            if len(deep_compare(prev_payload['sourceAmount'], payload['sourceAmount'], {})) > 0:
                old_source_amounts.append(prev_payload['sourceAmount'])

        prev_payload = payload

        if violations:
            version_violations.append((version.commit_id, violations))
    if versionn['status'] == 'PENDING':
        general_warnings.append('Still pending')
    if old_amounts:
        general_warnings.append('Amount was previously ' + ' and '.join(
            lib.pretty_amount(a['minorUnits'], a['currency']) for a in old_amounts))

    if version_violations or general_violations or general_warnings:
        has_violations = version_violations or general_violations
        print('    feedItemUid', versionn['feedItemUid'], 'has', 'violations:' if has_violations else 'warnings:')
        for commit_id, violations in version_violations:
            print('    ', 'Commit', commit_id)
            for v in violations:
                print('     ', v)
        for v in general_violations:
                print('    ', v)
        for w in general_warnings:
                print('    ', w)
        return has_violations
    return False

def item_time(item):
    return max(version.payload['transactionTime'] for version in item)

def dump_category(feed_items):
    violations = False
    balance = 0
    currencies = set()
    for item in sorted(feed_items.values(), key=item_time):
        if dump_item(item):
            violations = True
        if item[-1].payload['status'] != 'DECLINED':
            currencies.add(item[-1].payload['amount']['currency'])
            balance += item[-1].payload['amount']['minorUnits'] * (
                1 if item[-1].payload['direction'] == 'IN' else -1)
    if len(currencies) > 1:
        print(' Category has multiple currencies!')
        return True
    if currencies:
        print(' Balance:', lib.pretty_amount(balance, next(iter(currencies))))
    return violations

def main():
    violations = False
    accounts = bankrepo.read_repo(
        os.path.expanduser('~/starling/.git'), has_categories=True)
    for account_id in sorted(accounts):
        print('Account', account_id)
        account = accounts[account_id]
        for category_id in sorted(account):
            print(' Category', category_id)
            if dump_category(account[category_id]):
                violations = True
    if violations:
        print('VIOLATIONS occurred!', file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
