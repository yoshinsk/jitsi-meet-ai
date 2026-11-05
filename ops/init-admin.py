#!/usr/bin/env python3
# File: ops/init-admin.py
# Summary: Create a private initial administrator hash without placing a plaintext password in deployment files.

import argparse
import getpass
import hashlib
import json
from pathlib import Path
import secrets


def create_seed(path, password):
    """Use the application's PBKDF2 format and refuse to overwrite existing credentials."""
    if not password:
        raise ValueError('The administrator password must not be empty')
    salt = secrets.token_hex(32)
    values = {'admin_salt': salt, 'admin_digest': hashlib.pbkdf2_hmac(
        'sha256', password.encode(), salt.encode(), 600000).hex()}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as output:
        path.chmod(0o600)
        json.dump(values, output, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('.local/admin-seed.json'))
    args = parser.parse_args()
    password = getpass.getpass('Administrator password: ')
    if password != getpass.getpass('Confirm administrator password: '):
        parser.error('Passwords do not match')
    create_seed(args.output, password)
    print('Administrator hash created. Keep the seed private; the login name is ubuntu.')


if __name__ == '__main__':
    main()
