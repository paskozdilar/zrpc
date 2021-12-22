# Generate a standalone executable containing ZRPC CLI

PYTHON_FILES := $(shell find zrpc -name '*.py')

.PHONY: all build install uninstall clean

all: build

build: bin/zrpc

install: build
	cp bin/zrpc /usr/local/bin/zrpc

uninstall: /usr/local/bin/zrpc
	rm -f /usr/local/bin/zrpc

bin/zrpc: Dockerfile $(PYTHON_FILES)
	docker build . -t zrpc-build && \
	container=$$(docker create zrpc-build) && \
	mkdir -p bin && \
	docker cp $$container:/build/dist/__main__ bin/zrpc && \
	docker rm $$container

clean:
	rm bin/zrpc
	rmdir bin
	docker image rm zrpc-build
