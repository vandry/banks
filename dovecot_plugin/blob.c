#include <stdio.h>
#include <git2.h>
#include <time.h>
#include <json-c/json.h>

#include "blob.h"

int
blob_get_date(json_object *p, time_t *ret)
{
	struct json_object *transactionTime;
	struct tm tm;
	int y, mo;
	float s;
	const char *iso8601;

	if (!json_object_is_type(p, json_type_object)) {
		return -1;
	}
	if (!json_object_object_get_ex(p, "transactionTime", &transactionTime)) {
		return -1;
	}
	if (!json_object_is_type(transactionTime, json_type_string)) {
		return -1;
	}
	iso8601 = json_object_get_string(transactionTime);
	if (sscanf(iso8601, "%d-%d-%dT%d:%d:%fZ", &y, &mo, &(tm.tm_mday), &(tm.tm_hour), &(tm.tm_min), &s) != 6) {
		return -1;
	}
	tm.tm_year = y - 1900;
	tm.tm_mon = mo - 1;
	tm.tm_sec = s;
	*ret = timegm(&tm);
	return 0;
}

json_object *
parse_blob(git_blob *blob)
{
	struct json_tokener *tok;
	json_object *payload;

	tok = json_tokener_new();
	payload = json_tokener_parse_ex(tok, git_blob_rawcontent(blob), git_blob_rawsize(blob));
	json_tokener_free(tok);
	return payload;
}
