#include <git2.h>
#include "lib.h"
#include "str.h"

#include "find.h"

struct find_blob_ctx {
	const git_oid *blobid;
	const char *path;
};

static int
find_blob(const char *root, const git_tree_entry *entry, void *payload)
{
	struct find_blob_ctx *ctx = (struct find_blob_ctx *)payload;
	if (memcmp(ctx->blobid, git_tree_entry_id(entry), sizeof(*ctx->blobid)) == 0) {
		ctx->path = t_strdup_printf("%s%s", root, git_tree_entry_name(entry));
		return -1;  /* stop the search */
	}
	return 0;
}

int
find_versions(git_repository *repo, const git_oid *blobid, find_versions_callback_t cb, void *data)
{
	git_reference *head;
	const git_oid *headid;
	git_oid commitid;
	git_commit *head_commit, *commit;
	git_tree *tree;
	git_tree_entry *tentry;
	struct find_blob_ctx fb_ctx;
	git_revwalk *walk;
	int ret;

	/* Find this blob at head to get its path */

	if (git_repository_head(&head, repo)) {
		return -1;
	}
	headid = git_reference_target(head);
	if (git_commit_lookup(&head_commit, repo, headid) < 0) {
		git_reference_free(head);
		return -1;
	}
	if (git_commit_tree(&tree, head_commit) < 0) {
		git_reference_free(head);
		git_commit_free(head_commit);
		return -1;
	}
	fb_ctx.blobid = blobid;
	fb_ctx.path = NULL;
	git_tree_walk(tree, GIT_TREEWALK_PRE, find_blob, &fb_ctx);
	if (fb_ctx.path == NULL) {
		git_commit_free(head_commit);
		git_reference_free(head);
		return -1;
	}
	git_commit_free(head_commit);
	head_commit = NULL;

	/* Backtrack from head */

	if (git_revwalk_new(&walk, repo) < 0) {
		git_reference_free(head);
		return -1;
	}
	git_revwalk_sorting(walk, GIT_SORT_TOPOLOGICAL);
	if (git_revwalk_push(walk, headid) < 0) {
		git_reference_free(head);
revwalk_error:
		git_revwalk_free(walk);
		return -1;
	}
	git_reference_free(head);
	head = NULL;
	headid = NULL;
	while ((ret = git_revwalk_next(&commitid, walk)) == 0) {
		if (git_commit_lookup(&commit, repo, &commitid) < 0) {
			goto revwalk_error;
		}
		if (git_commit_tree(&tree, commit) < 0) {
			git_commit_free(commit);
			goto revwalk_error;
		}
		if (git_tree_entry_bypath(&tentry, tree, fb_ctx.path) < 0) {
			ret = cb(commit, NULL, data);
		} else {
			ret = cb(commit, git_tree_entry_id(tentry), data);
			git_tree_entry_free(tentry);
		}
		git_tree_free(tree);
		if (ret < 0) {
			ret = GIT_ITEROVER;
			break;
		}
	}
	git_revwalk_free(walk);
	if (ret != GIT_ITEROVER) {
		return -1;
	}
	return 0;
}
