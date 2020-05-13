#ifndef _BANK_PLUGIN_COMMON_H_
#define _BANK_PLUGIN_COMMON_H_

#include <git2.h>
#include "lib.h"
#include "index-storage.h"

struct bank_mailbox {
	struct mailbox box;
	uint32_t bank_ext_id;

	git_repository *repo;
	const char *dirpath;
};

struct bank_mail_index_header {
        git_oid sync_commitid;
};

struct bank_mail_index_record {
        git_oid blobid;
};

#endif
