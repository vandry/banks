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
    'hasAttachment': None, # it seems fair that attachments are not immutable.
}

# It seems most terribly dodgy, but it looks like after 2022-04-03
# 'reference' changes on every settlement.
IGNORE_CHANGES_ON_SETTLEMENT = IGNORE_CHANGES.copy()
IGNORE_CHANGES_ON_SETTLEMENT['reference'] = None

TIMESTAMP_GRACE_PERIOD = datetime.timedelta(seconds=7)

# To account for the amount of time that passes between the time of
# the snapshot that the bank gives us and the time at which we commit
# the contents of that snapshot. We cannot insist that a transaction
# update that occurred between those two times must have been present
# in the commit even if its update time is earlier than the commit.
# As of 2020-06-21 the fetch script runs for about 18 seconds.
COMMIT_GRACE_PERIOD = datetime.timedelta(seconds=35)

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
        # The API change which was adapted for in commit
        # bb67b904bcc12a2a4d42234c752cc5cfb9c8e32f caused the cron job
        # to fail for several hours and one transaction was caught late
        # as a result.
        '327225ae1a90a18725057c027ba8d91ebe84d1bc': datetime.timedelta(hours=10),
        # A wedged instance of the cron job that held the lock for
        # a couple of weeks.
        '9d9ba6c7c6f1b60a02451098dbdc30009c4284aa': datetime.timedelta(days=17),
        # Downloads wedged between 2021-08-19 and 2021-08-25.
        'b9a4bb3f4219ef74c423d423b0802abb9bdd8bbd': datetime.timedelta(days=3),
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
    exceptions_by_commit_id = {
        # https://api.starlingbank.com/api/v2/accounts broken.
        # Returns an empty account list, which has broken fetching
        # and made us miss the first update.
        'adb2d6448c6392a57f3991baed8fb87ccbf9a147': datetime.timedelta(days=3),
        # A wedged instance of the cron job that held the lock for
        # a couple of weeks.
        '9d9ba6c7c6f1b60a02451098dbdc30009c4284aa': datetime.timedelta(days=17),
        # Downloads wedged between 2021-08-19 and 2021-08-25.
        'b9a4bb3f4219ef74c423d423b0802abb9bdd8bbd': datetime.timedelta(days=3),
    }

class LastUpdateDelay(MaxValuePolicy):
    default = datetime.timedelta(days=14)
    exceptions_by_commit_id = {
        # A bunch of TfL transactions were inexplicably updated
        # at 2019-11-26T16:25:35.262Z.
        'efb13f2ad9cbee1c3e396d8c49763da87eb9ff32': datetime.timedelta(days=225),
        # A single transaction from 2019-07-03 was inexplicably updated
        # on 2019-12-30 with no other apparent field changes.
        '92981dcdb2dfa44e7f33e6f1468986a39ecc815b': datetime.timedelta(days=181),
        # Settled after <1d but merchant name changed about 52d later.
        '1ef5fa7abf5d7443906317a36f1b63c7a3b0bd90': datetime.timedelta(days=53),
        # Settled after <1d but merchant name changed about 74d later.
        '319f41de33146fdbd6d6ca7733e849847c0bb455': datetime.timedelta(days=75),
        # A large number of transactions had their updatedAt field
        # inexplicably updated, with no other changes, between 2020-01-13
        # and 2020-01-20 inclusively.
        '5f9675d221abeb2132d843c8461cc72b88180040': datetime.timedelta(days=278),
        '9db6eb2ec928c3965850c5ebffc5594d857ddbfe': datetime.timedelta(days=278),
        '883c5e5732a6686d732385e73fc57a68252e37d8': datetime.timedelta(days=275),
        'e40d56c451e5eda1d9102c1aa929008b07316a20': datetime.timedelta(days=272),
        '69009c8d14b9a2afa95f71a044613f80bc967c59': datetime.timedelta(days=269),
        '39b75c3d5ad032770313f47dd811e983888b7722': datetime.timedelta(days=265),
        '6f637affd6e2270d1312356ce51d3040d9c365ae': datetime.timedelta(days=258),
        '81e91f4635e60bba1d38020469417c8aa5ba4f17': datetime.timedelta(days=264),
        '56d2e0d9ff92bd546b24a45fb3720835779114af': datetime.timedelta(days=264),
        '87c34e63f522d9735a24e53624722d985d699d5a': datetime.timedelta(days=255),
        'fe4c59e92f2d4bb2c122808ff77eb2a7b04a285d': datetime.timedelta(days=255),
        'aca6b57d9b7609e50851be9677601703cb761b4a': datetime.timedelta(days=251),
        'b4de9d405ddcbc43c9cd8a2678bfa80d05d7c349': datetime.timedelta(days=247),
        'ccf861bbab2e1edb8cb074ff3454571288fb5640': datetime.timedelta(days=245),
        '83c307fbbb3cec03977233ae93cb5725680c4094': datetime.timedelta(days=249),
        '6cda67ab79464cdee9c84d195d9720daa046de36': datetime.timedelta(days=248),
        'ce0741d9f7a46d61ae0ef4c1f1296a38ec8ddae6': datetime.timedelta(days=249),
        'd17781c56ac83b4186e75454297dbf2bfcb6eb5e': datetime.timedelta(days=247),
        '69bb71f5b8f78a0a6a97526689bf6e4f2728346b': datetime.timedelta(days=248),
        '338d7d1c789996af6cb11c87e3635af52f350961': datetime.timedelta(days=248),
        '208c074ebcfa8413cadb370af8b96821f161e3ac': datetime.timedelta(days=240),
        '288c5933baa80b4daa7e1508fcdfad249af35267': datetime.timedelta(days=235),
        '44f489397b1ee67b82c6e637f2d6c317d5337e4c': datetime.timedelta(days=236),
        '4eda95bc1f97bd17ffea28bdb8ba1614844de672': datetime.timedelta(days=223),
        'c8b6ffcf27972c06d61d6e3f1821c181e2c4a690': datetime.timedelta(days=222),
        '9488bb24a43b940e6c62c2c5e8de62fb032a78e0': datetime.timedelta(days=207),
        'f81daf507d70c40fae24963ea4538a6eea973891': datetime.timedelta(days=211),
        'a00280772cfc1f1684189b78f6623d18d686d161': datetime.timedelta(days=212),
        'a8f05da30e14979fd0ce8d361718f55b1920ae19': datetime.timedelta(days=206),
        'efc341851e4942d3cda11111b34a87e7a81d43df': datetime.timedelta(days=202),
        '9013d542da4d7de4439f05ea239b3b0c538c1642': datetime.timedelta(days=197),
        '04570d7d47702841d89e1b70a01d59c475e060a4': datetime.timedelta(days=199),
        '35eb5588d960fc3f8028686c5b645ee9dee762b4': datetime.timedelta(days=191),
        '79d4f87a1471babe2659b4920bdca528f47f27ad': datetime.timedelta(days=185),
        '06090b992f5606dad9a94386c3eb4d397f96adf6': datetime.timedelta(days=178),
        'cc69e4a1698a072b648d43cb05521ef6b1f2d7ce': datetime.timedelta(days=180),
        '35f82882e1a068335c2a93bad897866482282af0': datetime.timedelta(days=173),
        '115a0f4f1bcb9375c84dcc3c5d8bb39b0dddedfc': datetime.timedelta(days=167),
        'de8e25cec78066af2f2b065be8178d4307cdbc46': datetime.timedelta(days=151),
        '064c92fdab3c43edb9b377384b85081bb4202c9c': datetime.timedelta(days=153),
        '0f51a78d02d0ec1a06545abc5f5f84268bb5c0ff': datetime.timedelta(days=153),
        '4f0aa55e4558282a00ae85b81e871a8f1bc6573a': datetime.timedelta(days=149),
        'cc0b33a6095bb9afe075f257434acd9505677fd3': datetime.timedelta(days=145),
        'dc4e0cc91397860bebae7a5c6f4e7e67567c4589': datetime.timedelta(days=144),
        'a6fa80a4f1476392c2b1ef936c98eca7b696d937': datetime.timedelta(days=146),
        '84b79c177c9a12538b2ed86d1b039c0f433cdbf9': datetime.timedelta(days=129),
        'a09e6b245749d1e591aaa509d99a0c5ca0934269': datetime.timedelta(days=131),
        'efc72bd8b9d3b7b706c5ba79fb8f012c1a9bbabc': datetime.timedelta(days=131),
        '586df07b8002ea0922778bae687582ed3c42584c': datetime.timedelta(days=129),
        '03cecbb709ad4f5d251b1455db3142b641289c2f': datetime.timedelta(days=128),
        'db454f37ae45d1cf9f63ace5425dbd9155a71903': datetime.timedelta(days=126),
        '87e0d91133d53ee16009ade87fc3a91726bd725c': datetime.timedelta(days=116),
        'd996b7f4457ecb32f198cd3215e79275989efa72': datetime.timedelta(days=97),
        '5fb4302c1f64dba71581bb75c215eca4d247d0df': datetime.timedelta(days=89),
        'c9aa587d5702bcb6f5717a5873b836de2b8bf37a': datetime.timedelta(days=80),
        'ed9019d1d087a2fe850419ec164c83183cad2611': datetime.timedelta(days=63),
        '3811a8571985fcb53fb62bc636d2a9f5cfce8dad': datetime.timedelta(days=55),
        'd12917966d8739e760e14fa9cd5afe30897e6a9c': datetime.timedelta(days=54),
        '4a698946eb5312ebd29959161e8f9e8165acb254': datetime.timedelta(days=43),
        '8891828fece95a20ff746462306d68b7604be2d8': datetime.timedelta(days=43),
        '1e3c8f5c7795e035a6227c2b42f46fea9b4eb147': datetime.timedelta(days=30),
        'a1fad22c46b357bb8ccd94177ea71b6bc16ec057': datetime.timedelta(days=32),
        'd8befc11e878b5f59327434163713390276a93d6': datetime.timedelta(days=16),
        'e3187b35b30e204536e16023681f5c98c8aeec00': datetime.timedelta(days=20),
        'f74cce73da1dbc4b43aa9085998d1dae68b8fb41': datetime.timedelta(days=15),
        # The same (on 2020-01-15) but an additional change snuck in,
        # a transaction went from PENDING to SETTLED.
        '327225ae1a90a18725057c027ba8d91ebe84d1bc': datetime.timedelta(days=264),
        # The same (on 2020-01-17) but an additional change snuck in,
        # a new inbound Faster payment on 2020-01-17.
        '10970f4c6d16d830ce4b6985e79ce03ec9b9076d': datetime.timedelta(days=198),
        # A large number of transactions had their updatedAt field
        # inexplicably updated, with no other changes, between 2020-03-10 and
        # 2020-03-23.
        '96210d8c0f7608f95e49827b24cff9dd0fd1fc3e': datetime.timedelta(days=302),
        'd9ecaa9b2d13f71a5c35a3580732b57e20e6e14f': datetime.timedelta(days=314),
        'b2861c2d501b092df3fd2a493af81bede6b6689b': datetime.timedelta(days=310),
        'e688fa5823f5f3be527a112adcd77ce9c9e24762': datetime.timedelta(days=311),
        'c919d0e8b034e72829a75fdbb68b69b579b223a6': datetime.timedelta(days=305),
        'a326593b83380d6cb7ff638c3b207f5835a36dbd': datetime.timedelta(days=306),
        '176615756b6d4bad68f52ebc27d0556259243af2': datetime.timedelta(days=285),
        '81cf4b7028fb33b40a1d90cb8c267b8eef04d065': datetime.timedelta(days=252),
        '6214c79aad856365c21e116f0d0a5b8df6bccdfb': datetime.timedelta(days=233),
        'ab626400c09c3e09107e25f24c0fbe7997c527fd': datetime.timedelta(days=234),
        '39f612aee97210136af9a40fba52b1cfec35102b': datetime.timedelta(days=227),
        '0621bfed9904fc45f002c363d8bda8e93d525a29': datetime.timedelta(days=213),
        '11b653743d7458290e2065da8bd4d54a19530223': datetime.timedelta(days=214),
        '408aa136b292b783cecc24be17ccd6517bbf1629': datetime.timedelta(days=213),
        '9b194f51d8e236ea9ca52cd5fef6b44e0929b46f': datetime.timedelta(days=212),
        '12d9b51a6d07729cd13c2e0a5ef8ed8fdb713902': datetime.timedelta(days=158),
        'e56c47902500f768d04ab03d408ef8aeaaefd2d2': datetime.timedelta(days=84),
        # Some transactions had their updatedAt field inexplicably updated,
        # with no other changes, on 2021-12-04.
        '5d4705cb1c408521a2bbcedf85caa5aadf401446': datetime.timedelta(days=865),
        '107055ec56bee0731ed551c3d7b91b539117a808': datetime.timedelta(days=674),
        # Both arms of a transfer from GBP to EUR had updatedAt modified
        # nearly 3 years after the fact with no other change.
        '932515816d7c228f1246b700c665ced9bb7e594f': datetime.timedelta(days=1024),
        # Single transaction, updatedAt changed 8 months later.
        'a70c6ff47052ef59858c801d96030091a131d596': datetime.timedelta(days=238),
        # Several payments to the same payee had their updatedAt fields
        # changed with no other visible changes. I happen to remember that
        # I did a payee name check at this time, so we now know that such
        # things have that effect!
        '97017fc832a948e718d38e0afd6cc0d94a5c3cf9': datetime.timedelta(days=998),
        # 2 transactions from TVMs at separate stations with updatedAt nonsense.
        '3dd142cce650b10dcdf862830d28af4f3adf4d6b': datetime.timedelta(days=778),
        # more single transactions with updatedAt as sole change much later.
        '5dcf9751ba8239c645bbcb027d9150074701232a': datetime.timedelta(days=1118),
        '509573385de3197634c7a0bc34c2f66a3c9a4377': datetime.timedelta(days=1073),
        '00562c5ca9aba6f7773bbfdd9371a0132fa05f65': datetime.timedelta(days=258),
        '00da3abc8c4500f99e7ad5f8f24e97f2e25a4e8e': datetime.timedelta(days=217),
        'e074b78454427bf1a9f3568539a5c99b256098e7': datetime.timedelta(days=92),
        'c691b0d7576976c0472de390d6ba4f1c831e8a03': datetime.timedelta(days=99),
    }

class LastUpdateWarningDelay(LastUpdateDelay):
    """Like LastUpdateDelay but less strict; for a warning only"""
    default = datetime.timedelta(days=3)
    exceptions_by_counterPartyUid = {
        # Transport for London. Due to the weekly cap thing, they
        # keep transactions in the pending state for a long time.
        'f45c75f3-7954-454a-beb8-76133a4ca3da': datetime.timedelta(days=13),
    }

class StuffChangedExceptions(MaxValuePolicy):
    default = False  # stuff not allowed to change
    exceptions_by_commit_id = {
        # Unexplained change to counterPartyUid and counterPartySubEntityUid
        '3f66e5be26819d57d0a366a1e88b82fbf16c75b5': True,
        # Unexplained change to counterPartyUid and counterPartySubEntityUid
        'ea5b7bcd84cfd8587ab10f87709dfc1a4b73ce1a': True,
        # Unexplained change to counterPartySubEntityUid
        'a5854dfbed67c1d71b1f4c7bd552814059461fd8': True,
        # Unexplained diff to counterPartyUid and counterPartySubEntityUid
        '9d9f6c9e580c46ab1f15e4e67f1f9beef21b1773': True,
        # Spelling of counterPartyName was corrected (should this just be ignored?)
        '319f41de33146fdbd6d6ca7733e849847c0bb455': True,
        # Spelling of counterPartyName was corrected (should this just be ignored?)
        '1ef5fa7abf5d7443906317a36f1b63c7a3b0bd90': True,
        # Unexplained change to counterPartyUid and counterPartySubEntityUid
        '9d9f6c9e580c46ab1f15e4e67f1f9beef21b1773': True,
        # Unexplained change to counterPartyUid and counterPartySubEntityUid
        '854be5442977318fb08b1ccddf3c52995f36829a': True,
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
    # A bunch of transactions grew a hasAttachment field without updatedAt
    # being updated, leading us to conclude the change was backdated. That
    # seems to be a legitimate new field in the API.
    '9b48fdba2e9903f7215db875b41a6d02c9de119a',
    # A bunch of transactions grew a transactingApplicationUserUid field
    # without updatedAt being updated, leading us to conclude the change
    # was backdated. That seems to be a legitimate new field in the API.
    'b4792d7c245ea9f0652502daea54842b73d12880',
    # Contains a transaction that is future-dated by 40 seconds or so.
    'c2bf84e56b88835b9d08afb494b005bb04600fa4',
    # https://api.starlingbank.com/api/v2/accounts broken.
    # Returns an empty account list, which has broken fetching.
    'adb2d6448c6392a57f3991baed8fb87ccbf9a147',
    # A large number of transactions grew a new 'hasReceipt' field.
    'd939f75f3c519a571d638375a8fb88203938a426',
    # updatedAt on SETTLED transaction 46 seconds before transactionTime
    '906ad8f6fd0f0f24823dc818f01bf9af693c9fb5',
    # 2022-04-03 .. 2022-05-20 several attributes changed upon settlement:
    # - 'reference', 'counterPartyUid', 'counterPartySubEntityUid'
    '2a3479effdadb60c689230ff31f2f88989eb6c4b',
    '72633050b8afb1f11170b19f64c6dbdef4c106bd',
    'bdb32f88dac8797b47fdd0133bc84922a0462d49',
    'c88a606a9126c620273c7a525463492698cbc5c1',
    # - 'reference', 'counterPartyName',
    #   'counterPartyUid', 'counterPartySubEntityUid'
    'bfe83f2e8794c726063d4fd441c0c6f9ed1749ac',
    '938f24b8743b61ba92a2f7dbea689ca5cc95f309',
    '256ab9d34e6cddabf08bfafe2a0e70517007c063',
    'd65bfed1092e176322cb8abae9122cff4318c122',
    '54a5c44d994c761a6e43d4687af1502de285f231',  # merchant completely renamed!
    # Transaction was updated 16 seconds before it happened.
    '4e539110884299410451444f52e40e325ef5f28e',
    # On 2022-06-29 a new field 'batchPaymentDetails' was
    # introduced and mass-backfilled. In this commit, only
    # that field was updated.
    '5dcd6abe46ee633424235ab16d5d32e5a65e1d8a',
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
    if update0_time > transaction_time + FirstUpdateDelay.get_max(item[0]):
        general_violations.append('Transaction first updated too late (%s)' % (update0_time - transaction_time))
    updaten_time = lib.parse_iso8601(versionn['updatedAt'])

    version_violations = []
    version_warnings = []
    prev_payload = None
    old_amounts = []
    old_source_amounts = []
    for version in item:
        if str(version.commit_id) in WHITELISTED_COMMITS:
            prev_payload = version.payload
            continue

        violations = []
        warnings = []
        payload = version.payload

        if payload['direction'] not in ('IN', 'OUT'):
            violations.append('unrecognized direction %s' % version['direction'])
        if payload['status'] in ('PENDING', 'UPCOMING', 'DECLINED', 'REVERSED'):
            if 'settlementTime' in payload:
                violations.append(payload['status'] + ' transaction has a settlementTime')
        elif payload['status'] == 'SETTLED':
            if 'settlementTime' not in payload:
                violations.append('SETTLED transaction has no settlementTime')
        else:
            violations.append('unrecognized status %s' % payload['status'])

        update_time = lib.parse_iso8601(payload['updatedAt'])
        if update_time < version.prev_commit_time - COMMIT_GRACE_PERIOD:
            violations.append('transaction was updated at %s while transactions updated before %s should have been covered in a parent commit' % (
                update_time, version.prev_commit_time))
        if update_time > version.commit_time + TIMESTAMP_GRACE_PERIOD:
            violations.append('transaction with future date: it was updated at %s but committed at %s' % (
                update_time, version.commit_time))
        if (
            update_time < version.commit_time - MaxCommitDelay.get_max(version)
        ):
            violations.append('Took too long (%s) to commit' % (version.commit_time - update_time))
        if update_time > transaction_time + LastUpdateDelay.get_max(version):
            violations.append('Transaction updated too late (%s)' % (update_time - transaction_time))
        elif update_time > transaction_time + LastUpdateWarningDelay.get_max(version):
            warnings.append('Updated quite a long time after the transaction (%s)' % (update_time - transaction_time))

        if payload['status'] == 'UPCOMING':
            if transaction_time < update_time - TIMESTAMP_GRACE_PERIOD:
                violations.append('Upcoming transaction is not in the future')
        else:
            if transaction_time > update_time + TIMESTAMP_GRACE_PERIOD:
                violations.append('Transaction time %s greater than update time %s' % (transaction_time, update_time))

        if prev_payload is not None:
            settling = prev_payload['status'] == 'PENDING' and payload['status'] == 'SETTLED'
            if not StuffChangedExceptions.get_max(version):
                ignorable = IGNORE_CHANGES
                if settling:
                    ignorable = IGNORE_CHANGES_ON_SETTLEMENT
                for c in deep_compare(prev_payload, payload, ignorable):
                    violations.append('%s changed between versions' % c)
            # This actually happens, apparently legitimately, for unexplained reasons
            # if len(deep_compare(prev_payload, payload, {'updatedAt': None})) == 0:
            #     violations.append('updatedAt changed between versions with no other change')

            if payload['updatedAt'] < prev_payload['updatedAt']:
                violations.append('updatedAt went backwards')

            settled = False
            if settling:
                settled = True
            elif prev_payload['status'] == 'UPCOMING' and payload['status'] == 'SETTLED':
                settled = True
            elif prev_payload['status'] == 'UPCOMING' and payload['status'] == 'PENDING':
                pass
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
        if warnings:
            version_warnings.append((version.commit_id, warnings))
    if versionn['status'] == 'PENDING':
        general_warnings.append('Still pending')
    if old_amounts:
        general_warnings.append('Amount was previously ' + ' and '.join(
            lib.pretty_amount(a['minorUnits'], a['currency']) for a in old_amounts))

    if version_violations or general_violations or version_warnings or general_warnings:
        has_violations = version_violations or general_violations
        print('    feedItemUid', versionn['feedItemUid'], 'has', 'violations:' if has_violations else 'warnings:')
        for commit_id, violations in version_violations:
            print('    ', 'Commit', commit_id)
            for v in violations:
                print('     ', v)
        for commit_id, warnings in version_warnings:
            print('    ', 'Commit', commit_id)
            for w in warnings:
                print('     ', w)
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
