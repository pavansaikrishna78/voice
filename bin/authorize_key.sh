#!/usr/bin/env bash
# authorize_key.sh "<one public key line>"
# Lets that key log in as this user. Safe to re-run; the key is added only once.
set -u
if [ $# -ne 1 ]; then echo 'usage: authorize_key.sh "ssh-ed25519 AAAA... comment"' >&2; exit 2; fi
key=$1
case "$key" in
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*\ *|sk-ssh-*\ *) ;;
    *) echo "That is not a public key line. It should start with ssh-ed25519 or ssh-rsa." >&2; exit 1 ;;
esac
if ! printf '%s\n' "$key" | ssh-keygen -l -f - >/dev/null 2>&1; then
    echo "That public key is not valid. Copy the whole single line from the .pub file." >&2; exit 1
fi
umask 077
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if grep -qxF -- "$key" "$HOME/.ssh/authorized_keys"; then
    echo "This key was already authorized for $(id -un)."
else
    printf '%s\n' "$key" >> "$HOME/.ssh/authorized_keys"
    echo "Key added. It can now log in as $(id -un)."
fi
