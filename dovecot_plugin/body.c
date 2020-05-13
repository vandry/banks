#include <stdio.h>
#include <git2.h>
#include <time.h>
#include "lib.h"
#include "array.h"
#include "str.h"
#include "istream.h"
#include "istream-concat.h"

#include "body.h"
#include "find.h"

static const char *part_header_template = "\ncommit %s\nDate: %s\n\n";
static const char *part_header_date_template = "%Y-%m-%d %H:%M:%SZ";
static const int part_header_size = sizeof("\ncommit ")-1 + GIT_OID_HEXSZ + sizeof("\nDate: ")-1 + 20 + sizeof("\n\n")-1;
static const char *deleted_marker = "<deleted>\n";

struct commit_and_blob {
	/* Filled in at construction time */
	git_commit *commit;
	git_oid blobid;
	int exists;

	/* only valid during construction */
	struct commit_and_blob *next;

	/* Filled in later on demand */
	git_blob *blob;
};

struct commit_seq_body {
	/* Filled in at construction time */
	pool_t pool;
	int n_revisions;
	struct commit_and_blob *revisions;

	/* Filled in later on demand */
	char **part_headers;
	string_t **diffs;
};

void
commit_seq_body_free(struct commit_seq_body *b)
{
	int i;

	if (b->diffs != NULL) {
		for (i = 0; i < (b->n_revisions-1); i++) {
			str_free(&(b->diffs[i]));
		}
		p_free(b->pool, b->diffs);
	}
	for (i = 0; i < b->n_revisions; i++) {
		git_commit_free(b->revisions[i].commit);
		if (b->revisions[i].blob != NULL) {
			git_blob_free(b->revisions[i].blob);
		}
		if ((b->part_headers != NULL) && (b->part_headers[i] != NULL)) {
			p_free(b->pool, b->part_headers[i]);
		}
	}
	p_free(b->pool, b->revisions);
	if (b->part_headers != NULL) {
		p_free(b->pool, b->part_headers);
	}
	p_free(b->pool, b);
}

struct body_versions_context {
	int count;
	struct commit_and_blob *first;
	struct commit_and_blob *latest;
};

static int body_version(git_commit *commit, const git_oid *blobid, void *data)
{
	struct body_versions_context *ctx = (struct body_versions_context *)data;
	struct commit_and_blob *entry;
	struct commit_and_blob **prevp;

	prevp = (ctx->latest == NULL) ? &(ctx->first) : &(ctx->latest->next);
	entry = ctx->latest;
	if (blobid == NULL) {
		/* The path does not exist in this revision */
		if ((entry == NULL) || (entry->exists)) {
			/* and this is the first revision where that's true */
			ctx->latest = entry = t_new(struct commit_and_blob, 1);
			*prevp = entry;
			ctx->count++;
			entry->commit = commit;
		} else {
			/* which is the same as before.
			   This commit is earlier, so record this one instead
			   as the commit where this was first true. */
			git_commit_free(entry->commit);
			entry->commit = commit;
		}
		return 0;
	}
	if ((ctx->latest != NULL) && ctx->latest->exists && memcmp(&(ctx->latest->blobid), blobid, sizeof(*blobid)) == 0) {
		/* The path existed before and is the same in this revisio.
		   This commit is earlier, so record this one instead
		   as the commit where this was first true. */
		git_commit_free(entry->commit);
		entry->commit = commit;
	} else {
		ctx->latest = entry = t_new(struct commit_and_blob, 1);
		*prevp = entry;
		ctx->count++;
		entry->commit = commit;
		entry->blobid = *blobid;
		entry->exists = 1;
	}
	return 0;
}

struct commit_seq_body *
commit_seq_body_new(git_repository *repo, const git_oid *blobid, pool_t pool)
{
	int i, n_revisions;
	int omit_last = 0;
	struct commit_seq_body *b;
	struct body_versions_context bv_ctx;
	struct commit_and_blob *entry;

	memset(&bv_ctx, 0, sizeof(bv_ctx));
	if (find_versions(repo, blobid, body_version, &bv_ctx) < 0) {
		for (entry = bv_ctx.first; entry != NULL; entry = entry->next) {
			git_commit_free(entry->commit);
		}
		return NULL;
	}

	if ((bv_ctx.latest != NULL) && !(bv_ctx.latest->exists)) {
		/* The oldest entry indicates that the file did not exist.
		   We're not interested in that. */
		omit_last = 1;
		git_commit_free(bv_ctx.latest->commit);
	}
	n_revisions = bv_ctx.count - omit_last;

	b = p_new(pool, struct commit_seq_body, 1);
	b->pool = pool;
	b->n_revisions = n_revisions;
	b->revisions = p_new(pool, struct commit_and_blob, n_revisions);
	for (entry = bv_ctx.first, i = 0; entry != NULL; entry = entry->next) {
		if (omit_last && (entry->next == NULL)) continue;
		b->revisions[i++] = *entry;
	}

	return b;
}

static int
diff_file(const git_diff_delta *delta, float progress, void *payload)
{
	str_append((string_t *)payload,
		"\n"
		"diff below above\n"
		"--- below\n"
		"+++ above\n"
	);
	return 0;
}

static int
diff_hunk(const git_diff_delta *delta, const git_diff_hunk *hunk, void *payload)
{
	str_append((string_t *)payload, hunk->header);
	return 0;
}

static int
diff_line(const git_diff_delta *delta, const git_diff_hunk *hunk, const git_diff_line *line, void *payload)
{
	string_t *dest = (string_t *)payload;

	if (line->old_lineno == -1) {
		str_append_c(dest, '+');
	} else if (line->new_lineno == -1) {
		str_append_c(dest, '-');
	} else {
		str_append_c(dest, ' ');
	}
	str_append_data(dest, line->content, line->content_len);
	return 0;
}

static void
generate_diffs(struct commit_seq_body *b)
{
	int i;

	if (b->diffs != NULL) return;
	if (b->n_revisions < 2) return;
	b->diffs = p_new(b->pool, string_t *, b->n_revisions - 1);
	for (i = 0; i < (b->n_revisions)-1; i++) {
		b->diffs[i] = str_new(b->pool, 200);
		git_diff_blobs(
			b->revisions[i+1].blob, NULL,
			b->revisions[i].blob, NULL,
			NULL, /* options */
			diff_file, NULL, diff_hunk, diff_line,
			b->diffs[i]
		);
	}
}

static int
fetch_blobs(struct commit_seq_body *b, git_repository *repo)
{
	int i;

	for (i = 0; i < b->n_revisions; i++) {
		if (!(b->revisions[i].exists)) continue;
		if (b->revisions[i].blob == NULL) {
			if (git_blob_lookup(&(b->revisions[i].blob), repo, &(b->revisions[i].blobid)) != 0) {
				return -1;
			}
		}
	}
	return 0;
}

ssize_t
body_size(struct commit_seq_body *b, git_repository *repo)
{
	int i;
	size_t sum = 0;

	if (fetch_blobs(b, repo) < 0) return -1;
	for (i = 0; i < b->n_revisions; i++) {
		size_t payload_size;
		if (b->revisions[i].exists) {
			payload_size = git_blob_rawsize(b->revisions[i].blob);
		} else {
			payload_size = sizeof(deleted_marker) - 1;
		}
		sum += part_header_size + payload_size -
			((i == 0) ? 1 /* No initial newline */ : 0);
	}
	generate_diffs(b);
	for (i = 0; i < (b->n_revisions)-1; i++) {
		sum += str_len(b->diffs[i]);
	}
	return sum;
}

int
generate_body(struct commit_seq_body *b, git_repository *repo)
{
	int i;
	time_t when;
	struct tm tm;
	char date[40];
	char oid_text[GIT_OID_HEXSZ+1];

	if (b->part_headers != NULL) {
		return 0;
	}

	if (fetch_blobs(b, repo) < 0) {
		return -1;
	}

	b->part_headers = p_new(b->pool, char *, b->n_revisions);

	for (i = 0; i < b->n_revisions; i++) {
		b->part_headers[i] = p_new(b->pool, char, part_header_size+1);
		when = git_commit_time(b->revisions[i].commit);
		strftime(date, sizeof(date), part_header_date_template, gmtime_r(&when, &tm));
		git_oid_fmt(&(oid_text[0]), git_commit_id(b->revisions[i].commit));
		oid_text[GIT_OID_HEXSZ] = 0;
		sprintf(b->part_headers[i], part_header_template, oid_text, date);
	}
	generate_diffs(b);
	return 0;
}

struct istream *
body_stream(struct commit_seq_body *b, struct istream *header)
{
	struct istream **parts;
	struct istream *ret;
	int i = 0;
	int part;
	int offset = 1; /* skip the separator newline of the first block */
	git_blob *blob;

	T_BEGIN {
		parts = t_new(struct istream *,
			((header == NULL) ? 0 : 1) +
			/* blobs */
			(b->n_revisions * 2) +
			/* diffs */
			((b->n_revisions == 0) ? 0 : (b->n_revisions - 1)) +
			/* terminator */
			1
		);
		if (header != NULL) {
			parts[i++] = header;
			/* use the separator of the first block as the
			   separator for header and body */
			offset = 0;
		}
		for (part = 0; part < b->n_revisions; part++) {
			parts[i++] = i_stream_create_from_data(
				b->part_headers[part] + offset,
				part_header_size - offset
			);
			if (b->revisions[part].exists) {
				blob = b->revisions[part].blob;
				parts[i++] = i_stream_create_from_data(
					git_blob_rawcontent(blob),
					git_blob_rawsize(blob)
				);
			} else {
				parts[i++] = i_stream_create_from_data(deleted_marker, sizeof(deleted_marker)-1);
			}
			if (part < (b->n_revisions - 1)) {
				parts[i++] = i_stream_create_from_data(str_data(b->diffs[part]), str_len(b->diffs[part]));
			}
			offset = 0;
		}
		parts[i] = NULL;
		ret = i_stream_create_concat(&(parts[0]));
	} T_END;
	return ret;
}
