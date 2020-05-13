#ifndef _BANK_PLUGIN_REPO_H_
#define _BANK_PLUGIN_REPO_H_

struct bank_mailbox;
int repo_init(struct bank_mailbox *mbox, const char *repo_path, const char *dir_path);
int repo_sync(struct bank_mailbox *mbox);

/* Call the callback once for each path that may be watched
   to detect changes in the repo. */
void repo_watch_paths(struct bank_mailbox *mbox, void (*cb)(const char *, void *), void *);


#endif
