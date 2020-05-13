PREFIX=$(DESTDIR)/usr/lib
DOVECOT_INC=/usr/include/dovecot
DOVECOT_CFLAGS=-I$(DOVECOT_INC) -DHAVE_CONFIG_H
CFLAGS=-g -fPIC -shared -Wall -I$(DOVECOT_INC) -DHAVE_CONFIG_H

INSTALL_DEST=$(PREFIX)/dovecot/modules

OBJ=main.o repo.o mail.o body.o find.o blob.o

all: bank_plugin.so

main.o: main.c bank.h repo.h mail.h

repo.o: repo.c bank.h repo.h blob.h

mail.o: mail.c mail.h repo.h body.h bank.h find.h blob.h

blob.o: blob.c blob.h

body.o: body.c body.h find.h

find.o: find.c find.h

bank_plugin.so: $(OBJ)
	$(CC) -shared -o $@ $(OBJ) -lgit2 -ljson-c

clean:
	rm -rf $(OBJ) bank_plugin bank_plugin.so

install: bank_plugin.so
	mkdir -p $(INSTALL_DEST)
	install -g bin -o root -m 0644 bank_plugin.so $(INSTALL_DEST)
