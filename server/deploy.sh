#!/bin/bash
set -e

SERVER=root@167.172.210.118

echo "Building MAD server for Linux..."
cd "$(dirname "$0")"
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o mad-server .

echo "Copying binary to server..."
ssh $SERVER "mkdir -p /mad-server && systemctl stop mad.service 2>/dev/null || true"
scp mad-server $SERVER:/mad-server/mad-server

echo "Copying and installing service..."
scp mad.service $SERVER:/etc/systemd/system/mad.service
ssh $SERVER "systemctl daemon-reload && systemctl enable mad.service && systemctl restart mad.service"

echo "Updating Caddyfile..."
scp Caddyfile.combined $SERVER:/app-server/Caddyfile
ssh $SERVER "systemctl reload caddy || systemctl restart caddy"

echo "Done! MAD server should be live at https://mad.goteamup.io"
echo "Check status: ssh $SERVER systemctl status mad.service"
