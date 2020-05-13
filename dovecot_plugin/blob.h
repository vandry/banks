#ifndef _BANK_PLUGIN_BLOB_H_
#define _BANK_PLUGIN_BLOB_H_

#include <json-c/json.h>
#include <git2.h>

int blob_get_date(json_object *, time_t *);
json_object *parse_blob(git_blob *blob);

#endif
