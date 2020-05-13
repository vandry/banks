#ifndef _BANK_PLUGIN_MAIL_H_
#define _BANK_PLUGIN_MAIL_H_

#include "lib.h"
#include "index-mail.h"

struct mail_vfuncs bank_mail_vfuncs;
struct mail *bank_mail_alloc(
	struct mailbox_transaction_context *t,
	enum mail_fetch_field wanted_fields,
	struct mailbox_header_lookup_ctx *wanted_headers
);

#endif
