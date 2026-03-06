.PHONY: server-build server-run server-deps

server-deps:
	cd server && go mod tidy

server-build: server-deps
	mkdir -p bin
	cd server && go build -o ../bin/mad-server .

server-run: server-build
	./bin/mad-server
