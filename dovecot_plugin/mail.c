#include <stdio.h>
#include <json-c/json.h>
#include <time.h>
#include <git2.h>
#include "lib.h"
#include "hash.h"
#include "str.h"
#include "istream.h"
#include "mail-index.h"

#include "bank.h"
#include "mail.h"
#include "repo.h"
#include "body.h"
#include "find.h"
#include "blob.h"

struct bank_mail {
	struct index_mail imail;
	struct bank_mail_index_record rec;
	char oid_text[GIT_OID_HEXSZ+1];
	git_commit *commit;
	git_blob *blob;
	string_t *header_str;
	struct commit_seq_body *body;
};

static void
bank_mail_reset(struct bank_mail *bmail)
{
	if (bmail->commit != NULL) {
		git_commit_free(bmail->commit);
		bmail->commit = NULL;
	}
	if (bmail->blob != NULL) {
		git_blob_free(bmail->blob);
		bmail->blob = NULL;
	}
	if (bmail->header_str != NULL) {
		str_free(&(bmail->header_str));
	}
	if (bmail->body != NULL) {
		commit_seq_body_free(bmail->body);
		bmail->body = NULL;
	}
}

static void
bank_mail_close(struct mail *mail)
{
	bank_mail_reset((struct bank_mail *)mail);
	index_mail_close(mail);
}

static void
bank_mail_free(struct mail *mail)
{
	bank_mail_reset((struct bank_mail *)mail);
	index_mail_free(mail);
}

static void
bank_mail_set_seq(struct mail *mail, uint32_t seq, bool saving)
{
	struct bank_mail *bmail = (struct bank_mail *)mail;
	struct bank_mailbox *mbox = (struct bank_mailbox *)mail->box;
	const void *data;

	bank_mail_reset(bmail);

	mail_index_lookup_ext(mail->transaction->view, seq, mbox->bank_ext_id, &data, NULL);
	memcpy(&(bmail->rec), data, sizeof(bmail->rec));

	index_mail_set_seq(mail, seq, saving);
}

static int
get_commit_helper(git_commit *commit, const git_oid *blobid, void *data)
{
        struct bank_mail *bmail = (struct bank_mail *)data;
	if (blobid == NULL) {
		/* The file didn't exist at this commit.
		   We must have reached just before the point
		   when it first existed. Search no further. */
		return -1;
	}
	if (bmail->commit != NULL) {
		/* Discard a newer commit where it also existed. */
		git_commit_free(bmail->commit);
	}
	/* This is the earliest known commit so far where it exists. */
	bmail->commit = commit;
	return 0;
}

static int
get_commit(struct bank_mail *bmail, struct bank_mailbox *mbox)
{
	if (bmail->commit != NULL) {
		return 0;
	}
	if (find_versions(mbox->repo, &(bmail->rec.blobid), get_commit_helper, bmail) < 0) {
		return -1;
	}
	if (bmail->commit == NULL) {
		return -1;
	}
	return 0;
}

static int
bank_mail_get_special(struct mail *mail, enum mail_fetch_field field, const char **value_r)
{
	struct bank_mail *bmail = (struct bank_mail *)mail;
	struct bank_mailbox *mbox = (struct bank_mailbox *)mail->box;

        switch (field) {
        case MAIL_FETCH_FROM_ENVELOPE:
		if (bmail->commit != NULL) {
			*value_r = git_commit_author(bmail->commit)->email;
			return 0;
		}
		*value_r = "";
		if (get_commit(bmail, mbox) < 0) {
			return -1;
		}
		*value_r = git_commit_author(bmail->commit)->email;
                return 0;
        case MAIL_FETCH_STORAGE_ID:
		git_oid_fmt(&(bmail->oid_text[0]), &(bmail->rec.blobid));
		bmail->oid_text[GIT_OID_HEXSZ] = 0;
                *value_r = &(bmail->oid_text[0]);
                return 0;
        default:
                return index_mail_get_special(mail, field, value_r);
        }
}

static void
get_date(json_object *p, char *buf, size_t max)
{
	struct tm tm;
	time_t moment;

	if (blob_get_date(p, &moment) < 0) {
		buf[0] = 0;
		return;
	}
	strftime(buf, max, "%a, %e %b %Y %H:%M:%S +0000", gmtime_r(&moment, &tm));
}

static void
identify_counterparty(json_object *p, string_t *dest)
{
	struct json_object *item, *details_block;
	const char *label = NULL;
	int need_open = 0;
	int need_close = 0;
	int have_username = 0;

	if (!json_object_is_type(p, json_type_object)) {
		return;
	}
	if (
		(json_object_object_get_ex(p, "counterPartyName", &item)) &&
		(json_object_is_type(item, json_type_string))
	) {
		/* Starling */
		str_append(dest, json_object_get_string(item));
		need_open = need_close = 1;
	} else if (
		(json_object_object_get_ex(p, "details", &details_block)) &&
		(json_object_is_type(details_block, json_type_object))
	) {
		/* Wise */
		if ((
			(json_object_object_get_ex(details_block, "originator", &item)) &&
			(json_object_is_type(item, json_type_string))
		) || (
			(json_object_object_get_ex(details_block, "senderName", &item)) &&
			(json_object_is_type(item, json_type_string))
		) || (
			(json_object_object_get_ex(details_block, "merchant", &item)) &&
			(json_object_is_type(item, json_type_object)) &&
			(json_object_object_get_ex(item, "name", &item)) &&
			(json_object_is_type(item, json_type_string))
		)) {
			/* Various types of Wise transaction */
			str_append(dest, json_object_get_string(item));
			need_open = need_close = 1;
		} else if (
			(json_object_object_get_ex(p, "type", &item)) &&
			(json_object_is_type(item, json_type_string))
		) {
			/* For currency conversions we can consider that
			   the counterparty name is the "opposite" currency,
			   which depends. */
			const char *type = json_object_get_string(item);
			if (0 == strcmp(type, "DEBIT")) {
				label = "targetAmount";
			} else if (0 == strcmp(type, "CREDIT")) {
				label = "sourceAmount";
			}
			if (
				(label != NULL) &&
				(json_object_object_get_ex(details_block, label, &item)) &&
				(json_object_is_type(item, json_type_object)) &&
				(json_object_object_get_ex(item, "currency", &item)) &&
				(json_object_is_type(item, json_type_string))
			) {
				str_append(dest, json_object_get_string(item));
				need_open = need_close = 1;
			}
		}
		/* For type=MONEY_ADDED we got nothing */
	}
	if (
		(json_object_object_get_ex(p, "counterPartySubEntityUid", &item)) &&
		(json_object_is_type(item, json_type_string))
	) {
		if (need_open) {
			str_append(dest, " <");
			need_open = 0;
		}
		have_username = 1;
		str_append(dest, json_object_get_string(item));
	}
	if (
		(json_object_object_get_ex(p, "counterPartyUid", &item)) &&
		(json_object_is_type(item, json_type_string))
	) {
		if (need_open) {
			str_append(dest, " <");
			need_open = 0;
		} else {
			str_append(dest, ".");
		}
		have_username = 1;
		str_append(dest, json_object_get_string(item));
	}
	if (have_username) {
		str_append(dest, "@");
		if (
			(json_object_object_get_ex(p, "counterPartyType", &item)) &&
			(json_object_is_type(item, json_type_string))
		) {
			str_append(dest, json_object_get_string(item));
		}
	} else {
		if (need_open) {
			str_append(dest, " <");
			need_open = 0;
		}
		str_append(dest, "unknown@unknown");
	}
	if (need_close && (!need_open)) {
		str_append(dest, ">");
	}
}

static void
identify_subject(json_object *p, string_t *dest)
{
	struct json_object *amount_block, *item;
	const char *sign = "";
	const char *currency = "";
	const char *symbol;
	const char *format;
	int have_amount = 0;
	int need_factor = -1;
	double amount, factor;
	char amount_s[50];

	if (!json_object_is_type(p, json_type_object)) {
		return;
	}
	if (
		(json_object_object_get_ex(p, "direction", &item)) &&
		(json_object_is_type(item, json_type_string)) &&
		(strcmp(json_object_get_string(item), "OUT") == 0)
	) {
		sign = "-";
	}
	if (
		(json_object_object_get_ex(p, "amount", &amount_block)) &&
		(json_object_is_type(amount_block, json_type_object))
	) {
		if (
			(json_object_object_get_ex(amount_block, "currency", &item)) &&
			(json_object_is_type(item, json_type_string))
		) {
			currency = json_object_get_string(item);
		}
		if (json_object_object_get_ex(amount_block, "minorUnits", &item)) {
			need_factor = 1;  /* Starling */
		} else if (json_object_object_get_ex(amount_block, "value", &item)) {
			need_factor = 0;  /* Wise */
		}
		if (need_factor >= 0) {
			if (json_object_is_type(item, json_type_double)) {
				amount = json_object_get_double(item);
				have_amount = 1;
			} else if (json_object_is_type(item, json_type_int)) {
				amount = json_object_get_int(item);
				have_amount = 1;
			}
		}
	}
	if (have_amount) {
		if (strcmp(currency, "GBP") == 0) {
			symbol = "=C2=A3";
			factor = need_factor ? 0.01 : 1.0;
			format = "%s%s%.2f";
		} else if (strcmp(currency, "EUR") == 0) {
			symbol = "=E2=82=AC";
			factor = need_factor ? 0.01 : 1.0;
			format = "%s%s%.2f";
		} else if (strcmp(currency, "CAD") == 0) {
			symbol = "CAD$";
			factor = need_factor ? 0.01 : 1.0;
			format = "%s%s%.2f";
		} else if (strcmp(currency, "USD") == 0) {
			symbol = "USD$";
			factor = need_factor ? 0.01 : 1.0;
			format = "%s%s%.2f";
		} else {
			symbol = currency;
			factor = 1.0;  /* ? */
			format = "%s%s%f";
		}
		if (((*sign) == 0) && (amount < 0.0)) {
			amount *= -1.0;
			sign = "-";
		}
		snprintf(amount_s, sizeof(amount_s), format, sign, symbol, amount * factor);
		str_append(dest, amount_s);
	}
	if (
		(json_object_object_get_ex(p, "source", &item)) &&
		(json_object_is_type(item, json_type_string))
	) {
		/* Starling */
		if (have_amount) {
			str_append(dest, " via ");
		}
		str_append(dest, json_object_get_string(item));
		if (
			(json_object_object_get_ex(p, "sourceSubType", &item)) &&
			(json_object_is_type(item, json_type_string))
		) {
			str_append(dest, " ");
			str_append(dest, json_object_get_string(item));
		}
	} else if (
		(json_object_object_get_ex(p, "details", &item)) &&
		(json_object_is_type(item, json_type_object)) &&
		(json_object_object_get_ex(item, "type", &item)) &&
		(json_object_is_type(item, json_type_string))
	) {
		/* Wise */
		if (have_amount) {
			str_append(dest, " via ");
		}
		str_append(dest, json_object_get_string(item));
	}
}

static int
generate_header(struct bank_mail *bmail)
{
	struct bank_mailbox *mbox = (struct bank_mailbox *)bmail->imail.mail.mail.box;
	string_t *dest;
	char blob_text[GIT_OID_HEXSZ+1];
	const git_signature *commit_author;
	json_object *payload;
	char date[40];

	if (bmail->header_str != NULL) return 0;

	if (bmail->commit == NULL) {
		if (get_commit(bmail, mbox) < 0) {
			return -1;
		}
	}
	if (bmail->blob == NULL) {
		if (git_blob_lookup(&(bmail->blob), mbox->repo, &(bmail->rec.blobid)) != 0) {
			return -1;
		}
	}

	dest = str_new(bmail->imail.mail.data_pool, 256);

	git_oid_fmt(&(blob_text[0]), &(bmail->rec.blobid));
	blob_text[GIT_OID_HEXSZ] = 0;
	commit_author = git_commit_author(bmail->commit);

	payload = parse_blob(bmail->blob);
	if (payload == NULL) {
		return -1;
	}
	get_date(payload, &(date[0]), sizeof(date));

	str_append(dest, "Date: ");
	str_append(dest, date);
	str_append(dest, "\nFrom: ");
	identify_counterparty(payload, dest);
	str_append(dest, "\nMessage-ID: <");
	str_append(dest, blob_text);
	str_append(dest, "@git-blob-id>\nSubject: =?utf-8?Q?");
	identify_subject(payload, dest);
	str_append(dest, "?=\nTo: ");
	str_append(dest, commit_author->name);
	str_append(dest, " <");
	str_append(dest, commit_author->email);
	str_append(dest, ">\nMIME-Version: 1.0\nContent-Type: text/plain\n");
	json_object_put(payload);

	bmail->header_str = dest;
	return 0;
}

static int
bank_mail_get_header_stream(struct mail *mail, struct mailbox_header_lookup_ctx *headers, struct istream **stream_r)
{
	struct bank_mail *bmail = (struct bank_mail *)mail;

	if (generate_header(bmail) < 0) {
		return -1;
	}
	*stream_r = i_stream_create_from_data(str_data(bmail->header_str), str_len(bmail->header_str));
	return 0;
}

static int
bank_mail_get_stream(
	struct mail *_mail, bool get_body ATTR_UNUSED,
	struct message_size *hdr_size,
	struct message_size *body_size,
	struct istream **stream_r
)
{
	struct bank_mail *bmail = (struct bank_mail *)_mail;
	struct bank_mailbox *mbox = (struct bank_mailbox *)_mail->box;
	struct index_mail *mail = (struct index_mail *)_mail;
	struct index_mail_data *data = &mail->data;

	if (data->stream == NULL) {
		if (bmail->body == NULL) {
			bmail->body = commit_seq_body_new(mbox->repo, &(bmail->rec.blobid), bmail->imail.mail.data_pool);
			if (bmail->body == NULL) {
				return -1;
			}
		}
		if (generate_body(bmail->body, mbox->repo) < 0) {
			return -1;
		}
		if (generate_header(bmail) < 0) {
			return -1;
		}
		data->stream = body_stream(
			bmail->body,
			i_stream_create_from_data(str_data(bmail->header_str), str_len(bmail->header_str))
		);
		if (mail->mail.v.istream_opened != NULL) {
			if (mail->mail.v.istream_opened(_mail, &data->stream) < 0) {
				i_stream_unref(&data->stream);
				return -1;
			}
		}
	}
	return index_mail_init_stream(mail, hdr_size, body_size, stream_r);
}

static int
bank_mail_get_received_date(struct mail *mail, time_t *date_r)
{
	struct bank_mail *bmail = (struct bank_mail *)mail;
	struct bank_mailbox *mbox = (struct bank_mailbox *)mail->box;

	if (bmail->commit == NULL) {
		if (get_commit(bmail, mbox) < 0) {
			return -1;
		}
	}
	*date_r = git_commit_time(bmail->commit);
        return 0;
}

static int
bank_mail_get_physical_size(struct mail *mail, uoff_t *size_r)
{
	struct bank_mail *bmail = (struct bank_mail *)mail;
	struct bank_mailbox *mbox = (struct bank_mailbox *)mail->box;
	ssize_t bsize;

	if (bmail->body == NULL) {
		bmail->body = commit_seq_body_new(mbox->repo, &(bmail->rec.blobid), bmail->imail.mail.data_pool);
		if (bmail->body == NULL) {
			return -1;
		}
	}
	bsize = body_size(bmail->body, mbox->repo);
	if (bsize < 0) {
		return -1;
	}
	if (generate_header(bmail) < 0) {
		return -1;
	}
	*size_r = str_len(bmail->header_str) + 1 + bsize;
        return 0;
}

struct mail_vfuncs bank_mail_vfuncs = {
	bank_mail_close,
	bank_mail_free,
	bank_mail_set_seq,
	index_mail_set_uid,
	index_mail_set_uid_cache_updates,
	index_mail_prefetch,
	index_mail_precache,
	index_mail_add_temp_wanted_fields,

	index_mail_get_flags,
	index_mail_get_keywords,
	index_mail_get_keyword_indexes,
	index_mail_get_modseq,
	index_mail_get_pvt_modseq,
	index_mail_get_parts,
	index_mail_get_date,
	bank_mail_get_received_date,
	bank_mail_get_received_date,  /* actually save_date */
	index_mail_get_virtual_size,
	bank_mail_get_physical_size,
	index_mail_get_first_header,
	index_mail_get_headers,
	bank_mail_get_header_stream,
	bank_mail_get_stream,
	index_mail_get_binary_stream,
	bank_mail_get_special,
	index_mail_get_real_mail,
	index_mail_update_flags,
	index_mail_update_keywords,
	index_mail_update_modseq,
	index_mail_update_pvt_modseq,
	NULL,
	index_mail_expunge,
	index_mail_set_cache_corrupted,
	index_mail_opened,
	index_mail_set_cache_corrupted_reason
};

struct mail *
bank_mail_alloc(
        struct mailbox_transaction_context *t, 
        enum mail_fetch_field wanted_fields,
        struct mailbox_header_lookup_ctx *wanted_headers
)
{
	struct bank_mail *bmail;
	pool_t pool;

	pool = pool_alloconly_create("mail", 2048);
	bmail = p_new(pool, struct bank_mail, 1);
	bmail->imail.mail.pool = pool;
	index_mail_init(&(bmail->imail), t, wanted_fields, wanted_headers);
	return &bmail->imail.mail.mail;
}
