#include <stdlib.h>
#include <git2.h>
#include <json-c/json.h>
#include "lib.h"
#include "hash.h"
#include "ioloop.h"
#include "mail-index.h"

#include "repo.h"
#include "bank.h"
#include "blob.h"

#define SORT_ON_SYNC /* optional feature */

struct blob_info {
	char *filename;
	git_oid blobid;
	git_oid least_recent_commitid;
	struct blob_info *newer_version;
	int scan_mark;
};

static unsigned int
blob_hash(const git_oid *oid)
{
	return oid->id[0] | (oid->id[1] << 8) | (oid->id[2] << 16) | (oid->id[3] << 24);
}

static int
blob_cmp(const git_oid *a, const git_oid *b)
{
	return memcmp(a, b, sizeof(*a));
}

int
repo_init(struct bank_mailbox *mbox, const char *repo_path, const char *dir_path)
{
	int err;

	mbox->dirpath = p_strdup(mbox->box.pool, dir_path);
	err = git_repository_open(&(mbox->repo), repo_path);
	if (err != 0) {
		const git_error *error = giterr_last();
		mail_storage_set_critical(mbox->box.storage, "git(%s) error %d: %s", repo_path, err,
			(error && error->message) ? error->message : "???");
		return -1;
	}

	return 0;
}

static int
repo_get_head(struct bank_mailbox *mbox, git_oid *head_r)
{
	git_reference *head;

	if (git_repository_head(&head, mbox->repo)) {
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	*head_r = *git_reference_target(head);
	git_reference_free(head);
	return 0;
}

void
repo_watch_paths(struct bank_mailbox *mbox, void (*cb)(const char *, void *), void *data)
{
	git_reference *head;

	if (git_reference_lookup(&head, mbox->repo, "HEAD") != 0) return;
	cb(t_strdup_printf("%s/HEAD", git_repository_path(mbox->repo)), data);
	if (git_reference_type(head) == GIT_REF_SYMBOLIC) {
		cb(t_strdup_printf("%s/%s", git_repository_path(mbox->repo), git_reference_symbolic_target(head)), data);
	}
	git_reference_free(head);
}

struct blob_and_time {
	git_oid blobid;
	time_t timestamp;
};

#ifdef SORT_ON_SYNC
static int
bytime(const void *a, const void *b)
{
	time_t ta, tb;
	ta = (*((const struct blob_and_time **)a))->timestamp;
	tb = (*((const struct blob_and_time **)b))->timestamp;
	if (ta < tb) return -1;
	if (ta > tb) return 1;
	return 0;
}
#endif

static int
repo_scan(struct bank_mailbox *mbox, git_oid *head_commitid, struct mail_index_transaction *trans, struct mail_index_view *sync_view, uint32_t next_uid)
{
	const git_oid *treeid;
	git_oid *blobid;
	git_blob *blob;
	git_commit *head_commit;
	json_object *payload;
	git_tree *tree;
	git_tree_entry *entry;
	const git_tree_entry *e;
	size_t i, nfiles;
	uint32_t uid, seq;
	pool_t scanpool;
	struct blob_and_time *file_entry;
	HASH_TABLE(git_oid *, struct blob_and_time *) files;
	struct hash_iterate_context *iter;
	struct bank_mail_index_record index_rec, *brec;
	unsigned int n_entries;
	struct blob_and_time **file_entry_list;
	uint32_t messages_count, mseq;

	if (git_commit_lookup(&head_commit, mbox->repo, head_commitid) < 0) {
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	if (git_commit_tree(&tree, head_commit) < 0) {
		git_commit_free(head_commit);
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	git_commit_free(head_commit);
	if (git_tree_entry_bypath(&entry, tree, mbox->dirpath) < 0) {
		git_tree_free(tree);
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	git_tree_free(tree);
	if (git_tree_entry_type(entry) != GIT_OBJ_TREE) {
		git_tree_entry_free(entry);
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	treeid = git_tree_entry_id(entry);
	if (git_tree_lookup(&tree, mbox->repo, treeid) < 0) {
		git_tree_entry_free(entry);
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	git_tree_entry_free(entry);
	nfiles = git_tree_entrycount(tree);

	scanpool = pool_alloconly_create("repo_scan", 4096);
	hash_table_create(&files, scanpool, 0, blob_hash, blob_cmp);

	for (i = 0; i < nfiles; i++) {
		e = git_tree_entry_byindex(tree, i);
		if (git_tree_entry_type(e) != GIT_OBJ_BLOB) {
			continue;
		}
		file_entry = p_new(scanpool, struct blob_and_time, 1);
		file_entry->blobid = *git_tree_entry_id(e);
		hash_table_insert(files, &(file_entry->blobid), file_entry);
	}

	git_tree_free(tree);

	messages_count = mail_index_view_get_messages_count(sync_view);
        for (mseq = 1; mseq <= messages_count; mseq++) {
		mail_index_lookup_ext(sync_view, mseq, mbox->bank_ext_id, (const void **)(&brec), NULL);
		file_entry = hash_table_lookup(files, &(brec->blobid));
		if (file_entry == NULL) {
                	mail_index_expunge(trans, mseq);
		} else {
			hash_table_remove(files, &(brec->blobid));
		}
        }

	n_entries = hash_table_count(files);
	if (n_entries == 0) {
		pool_unref(&scanpool);
		return 0;
	}

	file_entry_list = p_new(scanpool, struct blob_and_time *, n_entries);
	i = 0;

	iter = hash_table_iterate_init(files);
	while (hash_table_iterate(iter, files, &blobid, &file_entry)) {
#ifdef SORT_ON_SYNC
		/* Fetching the transaction time makes sync a little
		   slower, and all it does is cause the uids to be
		   assigned more or less in transaction time sequence. */
		if (git_blob_lookup(&blob, mbox->repo, blobid) == 0) {
			payload = parse_blob(blob);
			if (payload != NULL) {
				blob_get_date(payload, &(file_entry->timestamp));
				json_object_put(payload);
			}
			git_blob_free(blob);
		}
#endif
		file_entry_list[i++] = file_entry;
	}
	hash_table_iterate_deinit(&iter);
#ifdef SORT_ON_SYNC
	qsort(file_entry_list, n_entries, sizeof(*file_entry_list), bytime);
#endif

	uid = next_uid;
	for (i = 0; i < n_entries; i++) {
		index_rec.blobid = file_entry_list[i]->blobid;
		mail_index_append(trans, uid, &seq);
		mail_index_update_ext(trans, seq, mbox->bank_ext_id, &index_rec, NULL);
		mailbox_recent_flags_set_uid(&mbox->box, uid);
		uid++;
	}

	pool_unref(&scanpool);
	return 0;
}

int
repo_sync(struct bank_mailbox *mbox)
{
	enum mail_index_sync_flags sync_flags;
	struct mail_index_sync_ctx *index_sync_ctx;
	struct mail_index_view *sync_view;
	struct mail_index_sync_rec sync_rec;
	struct mail_index_transaction *trans;
	const struct mail_index_header *hdr;
	git_oid head_commitid;
	struct bank_mail_index_header index_header;
	uint32_t new_uidv;
	int ret;
	int need_scan = 0;
	const void *data;
	size_t data_size;

	sync_flags = index_storage_get_sync_flags(&mbox->box) |
		MAIL_INDEX_SYNC_FLAG_FLUSH_DIRTY
		/* | MAIL_INDEX_SYNC_FLAG_REQUIRE_CHANGES */;
	ret = mail_index_sync_begin(mbox->box.index, &index_sync_ctx, &sync_view, &trans, sync_flags);
	if (ret <= 0) {
		if (ret < 0) mailbox_set_index_error(&mbox->box);
		return ret;
	}

	if (repo_get_head(mbox, &head_commitid) < 0) {
		mailbox_set_index_error(&mbox->box);
		return -1;
	}

	hdr = mail_index_get_header(sync_view);
	if (hdr->uid_validity == 0) {
		new_uidv = ioloop_time;
		mail_index_update_header(trans, offsetof(struct mail_index_header, uid_validity), &new_uidv, sizeof(new_uidv), TRUE);
		need_scan = 1;
	} else {
		mail_index_get_header_ext(sync_view, mbox->bank_ext_id, &data, &data_size);
		if (
			(data_size != sizeof(index_header)) ||
			(memcmp(&(((struct bank_mail_index_header *)data)->sync_commitid), &head_commitid, sizeof(head_commitid)) != 0)
		) {
			need_scan = 1;
		}
	}

	if (need_scan) {
		if (repo_scan(mbox, &head_commitid, trans, sync_view, hdr->next_uid) < 0) {
			mailbox_set_index_error(&mbox->box);
		}
		index_header.sync_commitid = head_commitid;
		mail_index_update_header_ext(trans, mbox->bank_ext_id, 0, &index_header, sizeof(index_header));
	}

	/* I believe this iterates over things that Dovecot wants us to do,
	   like expunge messages or set flags. But we are read only. */
	while (mail_index_sync_next(index_sync_ctx, &sync_rec)) ;

	if (mail_index_sync_commit(&index_sync_ctx) < 0) {
		mailbox_set_index_error(&mbox->box);
		return -1;
	}
	return 0;
}
