#!/usr/bin/python3
# -*- coding: utf-8 -*-

import collections
import datetime
import json
import sys
import pygit2


Transaction = collections.namedtuple('Transaction', ('payload', 'blob_id', 'commit_id', 'commit_time', 'prev_commit_time'))


def read_repo(path, has_categories=False):
    accounts = {}
    have_transactions = set()
    repo = pygit2.Repository(path)
    prev_time = datetime.datetime(2000, 1, 1)
    for commit in repo.walk(repo.head.target, pygit2.GIT_SORT_TOPOLOGICAL|pygit2.GIT_SORT_REVERSE):
        cur_time = datetime.datetime.utcfromtimestamp(commit.commit_time)
        for entry1 in commit.tree:
            if entry1.type != 'tree':
                print('Warning: non-tree', entry1.id, 'at root level', file=sys.stderr)
                continue
            account = accounts.setdefault(entry1.name, {})
            if has_categories:
                # Categories are apparently an extra level of isolation within
                # each account. All of my accounts have only one category each
                # so I don't don't what they mean.
                categories = []
                for entry2 in repo[entry1.id]:
                    if entry2.type != 'tree':
                        print('Warning: non-tree', entry2.id, 'at account level', file=sys.stderr)
                        continue
                    categories.append((entry2.name, entry2.id))
            else:
                categories = [(None, entry1.id)]
            for category_name, category_id in categories:
                category = account.setdefault(category_name, {})
                for entry3 in repo[category_id]:
                    if entry3.type != 'blob':
                        print('Warning: non-blob', entry3.id, 'at category level', file=sys.stderr)
                        continue
                    transaction = category.setdefault(entry3.name, [])
                    if transaction and transaction[-1].blob_id == entry3.id:
                        continue
                    blob = repo[entry3.id]
                    transaction.append(Transaction(
                        payload=json.loads(blob.data.decode('utf-8')),
                        blob_id=entry3.id,
                        commit_id=commit.id,
                        commit_time=cur_time,
                        prev_commit_time=prev_time,
                    ))
        prev_time = cur_time
    return accounts
