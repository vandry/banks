#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <git2.h>
#include "lib.h"
#include "array.h"
#include "istream.h"
#include "module-context.h"
#include "mail-copy.h"
#include "mail-index.h"
#include "mail-namespace.h"
#include "index-mail.h"
#include "index-storage.h"
#include "mailbox-list-private.h"

#include "bank.h"
#include "repo.h"
#include "mail.h"

struct bank_storage {
	struct mail_storage storage;
};

#define BANK_LIST_CONTEXT(obj) MODULE_CONTEXT(obj, bank_mailbox_list_module)

struct bank_mailbox_list {
	union mailbox_list_module_context module_ctx;
};

static MODULE_CONTEXT_DEFINE_INIT(bank_mailbox_list_module, &mailbox_list_module_register);

struct mail_storage bank_storage;

static struct mail_storage *
bank_storage_alloc(void)
{
	struct bank_storage *storage;
	pool_t pool;

	pool = pool_alloconly_create("bank storage", 512+256);
	storage = p_new(pool, struct bank_storage, 1);
	storage->storage = bank_storage;
	storage->storage.pool = pool;
	return &storage->storage;
}

static void
bank_storage_add_list(struct mail_storage *storage, struct mailbox_list *list)
{
	struct bank_mailbox_list *mlist;

	mlist = p_new(list->pool, struct bank_mailbox_list, 1);
	mlist->module_ctx.super = list->v;

	list->ns->flags |= NAMESPACE_FLAG_NOQUOTA;

	MODULE_CONTEXT_SET(list, bank_mailbox_list_module, mlist);
}

static void
bank_storage_get_list_settings(const struct mail_namespace *ns, struct mailbox_list_settings *set)
{
	if (set->layout == NULL) set->layout = MAILBOX_LIST_NAME_FS;
}

static int
bank_mailbox_create(struct mailbox *box, const struct mailbox_update *update, bool directory)
{
	mail_storage_set_error(box->storage, MAIL_ERROR_NOTPOSSIBLE, "Can't create bank mailboxes");
	return -1;
}

static int
bank_mailbox_update(struct mailbox *box, const struct mailbox_update *update)
{
	mail_storage_set_error(box->storage, MAIL_ERROR_NOTPOSSIBLE, "Can't update bank mailboxes");
	return -1;
}

static int
bank_mailbox_get_metadata(struct mailbox *box, enum mailbox_metadata_items items, struct mailbox_metadata *metadata_r)
{
	if (index_mailbox_get_metadata(box, items, metadata_r) < 0) return -1;
	if ((items & MAILBOX_METADATA_GUID) != 0) {
		mail_storage_set_error(box->storage, MAIL_ERROR_NOTPOSSIBLE, "bank mailboxes have no GUIDs");
		return -1;
	}
	return 0;
}

static int
bank_mailbox_open(struct mailbox *box)
{
	int fd;
	const char *box_path, *line, *repo_filename, *path_filename;
	struct istream *input;
	struct bank_mailbox *mbox = (struct bank_mailbox *)box;

	box_path = mailbox_get_path(&mbox->box);
	repo_filename = t_strconcat(box_path, "/repo", NULL);
	path_filename = t_strconcat(box_path, "/path", NULL);
	fd = open(path_filename, O_RDONLY);
	if (fd < 0) {
		if (errno == ENOENT) {
			mail_storage_set_error(box->storage, MAIL_ERROR_NOTFOUND, T_MAIL_ERR_MAILBOX_NOT_FOUND(box->vname));
		} else {
			mail_storage_set_critical(box->storage, "open(%s) failed: %m", path_filename);
		}
		return -1;
	}
	input = i_stream_create_fd_autoclose(&fd, (size_t)-1);
	line = i_stream_read_next_line(input);
	if ((line == NULL) || ((*line) == 0)) {
		mail_storage_set_critical(box->storage, "nothing read from %s", path_filename);
		i_stream_unref(&input);
		return -1;
	}

	if (repo_init(mbox, repo_filename, line) < 0) {
		i_stream_unref(&input);
		return -1;
	}

	i_stream_unref(&input);

        if (index_storage_mailbox_open(box, FALSE) < 0) {
		git_repository_free(mbox->repo);
		mbox->repo = NULL;
		return -1;
	}

	mbox->bank_ext_id = mail_index_ext_register(
		mbox->box.index, "bank",
		/* header size = */ sizeof(struct bank_mail_index_header),
		/* record size = */ sizeof(struct bank_mail_index_record),
		/* record align = */ sizeof(uint32_t)
	);

	return 0;
}

static void
bank_mailbox_close(struct mailbox *box)
{
	struct bank_mailbox *mbox = (struct bank_mailbox *)box;

	git_repository_free(mbox->repo);
	mbox->repo = NULL;
	index_storage_mailbox_close(box);
}

static struct mailbox_sync_context *
bank_storage_sync_init(struct mailbox *box, enum mailbox_sync_flags flags)
{
	struct bank_mailbox *mbox = (struct bank_mailbox *)box;
	struct mailbox_sync_context *sync_ctx;
	int ret = 0;

	if (!box->opened) {
		if (mailbox_open(box) < 0) ret = -1;
	}

	if (index_mailbox_want_full_sync(&mbox->box, flags) && ret == 0) {
		ret = repo_sync(mbox);
	}

	sync_ctx = index_mailbox_sync_init(box, flags, ret < 0);
	return sync_ctx;
}

static void
mbox_watch_path(const char *path, void *data)
{
	mailbox_watch_add((struct mailbox *)data, path);
}

static void
bank_notify_changes(struct mailbox *box)
{
	if (box->notify_callback == NULL) {
		mailbox_watch_remove_all(box);
	} else {
		repo_watch_paths((struct bank_mailbox *)box, mbox_watch_path, box);
	}
}

static int
bank_mailbox_exists(struct mailbox *box, bool auto_boxes, enum mailbox_existence *existence_r)
{
	const char *path, *repo_path, *path_path;
	enum mail_error error;
	int ret;
	struct stat sbuf;

	ret = mailbox_get_path_to(box, MAILBOX_LIST_PATH_TYPE_MAILBOX, &path);
	if (ret < 0) {
		mailbox_list_get_last_error(box->list, &error);
		if (error != MAIL_ERROR_NOTFOUND) return -1;
		*existence_r = MAILBOX_EXISTENCE_NONE;
		return 0;
	}

	repo_path = t_strconcat(path, "/repo", NULL);
	if ((lstat(repo_path, &sbuf) < 0) || !S_ISLNK(sbuf.st_mode)) {
		*existence_r = MAILBOX_EXISTENCE_NOSELECT;
		return 0;
	}
	if ((stat(repo_path, &sbuf) < 0) || !S_ISDIR(sbuf.st_mode)) {
		*existence_r = MAILBOX_EXISTENCE_NOSELECT;
		return 0;
	}
	path_path = t_strconcat(path, "/path", NULL);
	if (stat(path_path, &sbuf) < 0) {
		*existence_r = MAILBOX_EXISTENCE_NOSELECT;
		return 0;
	}
	*existence_r = MAILBOX_EXISTENCE_SELECT;
	return 0;
}

struct mailbox bank_mailbox = {
	.v = {
		index_storage_is_readonly,
		index_storage_mailbox_enable,
		bank_mailbox_exists,
		bank_mailbox_open,
		bank_mailbox_close,
		index_storage_mailbox_free,
		bank_mailbox_create,
		bank_mailbox_update,
		index_storage_mailbox_delete,
		index_storage_mailbox_rename,
		index_storage_get_status,
		bank_mailbox_get_metadata,
		index_storage_set_subscribed,
		index_storage_attribute_set,
		index_storage_attribute_get,
		index_storage_attribute_iter_init,
		index_storage_attribute_iter_next,
		index_storage_attribute_iter_deinit,
		index_storage_list_index_has_changed,
		index_storage_list_index_update_sync,
		bank_storage_sync_init,
		index_mailbox_sync_next,
		index_mailbox_sync_deinit,
		NULL,
		bank_notify_changes,
		index_transaction_begin,
		index_transaction_commit,
		index_transaction_rollback,
		NULL,
		bank_mail_alloc,
		index_storage_search_init,
		index_storage_search_deinit,
		index_storage_search_next_nonblock,
		index_storage_search_next_update_seq,
		/* save_alloc = */ NULL,
		/* save_begin = */ NULL,
		/* save_continue = */ NULL,
		/* save_finish = */ NULL,
		/* save_cancel = */ NULL,
		mail_storage_copy,
		NULL,
		NULL,
		NULL,
		index_storage_is_inconsistent,
	}
};

static struct mailbox *
bank_mailbox_alloc(
	struct mail_storage *_storage, struct mailbox_list *list,
	const char *name, enum mailbox_flags flags
)
{
	struct bank_mailbox *mbox;
	pool_t pool;

	pool = pool_alloconly_create("bank mailbox", 1024+512);
	mbox = p_new(pool, struct bank_mailbox, 1);
	mbox->box = bank_mailbox;
	mbox->box.pool = pool;
	mbox->box.storage = _storage;
	mbox->box.list = list;
	mbox->box.mail_vfuncs = &bank_mail_vfuncs;

	index_storage_mailbox_alloc(&mbox->box, name, flags, "dovecot.index");

	mbox->bank_ext_id = (uint32_t)-1;
	return &mbox->box;
}

struct mail_storage bank_storage = {
	.name = "bank",
	.class_flags = 0,

	.v = {
		NULL,  /* get_setting_parser_info */
		bank_storage_alloc,
		NULL,  /* create */
		index_storage_destroy,
		bank_storage_add_list,
		bank_storage_get_list_settings,
		NULL,  /* autodetect */
		bank_mailbox_alloc,
		NULL,  /* purge */
		NULL,
	}
};

void
bank_plugin_init(struct module *module)
{
	git_libgit2_init();
	mail_storage_class_register(&bank_storage);
}

void
bank_plugin_deinit(void)
{
	mail_storage_class_unregister(&bank_storage);
}
