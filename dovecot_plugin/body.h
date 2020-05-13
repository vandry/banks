#ifndef _BANK_PLUGIN_BODY_H_
#define _BANK_PLUGIN_BODY_H_

#include <sys/types.h>
#include <git2.h>
#include "mempool.h"
#include "istream.h"

struct commit_seq_body *commit_seq_body_new(git_repository *, const git_oid *blobid, pool_t pool);

ssize_t body_size(struct commit_seq_body *, git_repository *);
int generate_body(struct commit_seq_body *, git_repository *);
void commit_seq_body_free(struct commit_seq_body *);
struct istream *body_stream(struct commit_seq_body *, struct istream *header);

#endif
