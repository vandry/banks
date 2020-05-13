#ifndef _BANK_PLUGIN_FIND_H_
#define _BANK_PLUGIN_FIND_H_

#include <git2.h>
#include "find.h"

#include "lib.h"

/* Called with a commit and an oid (which might be NULL if the file does
   not exist in that commit). If it returns -1, the search is stopped.
   The callback is expected to take ownership of the commit. */
typedef int (*find_versions_callback_t)(git_commit *, const git_oid *, void *);

/* Find all the commits where blob blobid or a previous version of the file
   at the same path existed. */
int find_versions(git_repository *, const git_oid *blobid, find_versions_callback_t, void *);

#endif
