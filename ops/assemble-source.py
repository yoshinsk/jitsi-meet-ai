#!/usr/bin/env python3
# File: ops/assemble-source.py
# Summary: Export the complete reviewed extension sources and manuals using an explicit allowlist that excludes runtime secrets.

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=datetime.now(timezone(timedelta(hours=9))).strftime('%Y%m%d'))
    selected = parser.parse_args()
    datetime.strptime(selected.date, '%Y%m%d')
    root = Path(__file__).resolve().parent.parent
    extensions = {'.py', '.ps1', '.sh', '.lua', '.json', '.conf', '.service', '.js', '.html', '.css', '.yml', '.yaml'}
    files = [root / name for name in ['requirements.txt', 'requirements-dev.txt', '.gitignore', '.gitattributes',
                                     '.env.example', 'LICENSE', 'NOTICE']]
    for folder in ['service', 'static', 'ops', 'tests', '.github']:
        files.extend(path for path in (root / folder).rglob('*')
                     if path.is_file() and path.suffix in extensions and '__pycache__' not in path.parts)
    files = sorted(files, key=lambda path: path.relative_to(root).as_posix())
    language = {'.py': 'python', '.ps1': 'powershell', '.sh': 'bash', '.lua': 'lua', '.json': 'json',
                '.js': 'javascript', '.html': 'html', '.css': 'css', '.conf': 'text', '.service': 'ini'}
    body = ['# jitsi-meet-ai コード全文', '',
            'この環境に追加したプログラムをファイルごとに全文掲載しています。差分表示ではありません。', '',
            'APIキー、管理者パスワード、内部中継トークン、会議の招待トークン、録画や発言データは含めていません。', '',
            'Jitsi本体と公式プロキシの上流ソースは、`THIRD_PARTY.md` の採用バージョンを参照してください。', '']
    for path in files:
        body.extend(['## ' + path.relative_to(root).as_posix(), '',
                     '`````' + language.get(path.suffix, 'text'), path.read_text(encoding='utf-8').rstrip(), '`````', ''])
    assets = sorted((root / 'assets' / 'announcements').glob('*.wav'))
    body.extend(['## 日本語案内音声', '', '生成済み音声は公開版に含めていません。台本と生成方法は `assets/announcements/README.md` を参照してください。', '',
                 '| ファイル | SHA-256 |', '| --- | --- |'])
    for path in assets:
        body.append(f'| {path.relative_to(root).as_posix()} | `{hashlib.sha256(path.read_bytes()).hexdigest()}` |')
    documents = root / 'Documents'
    documents.mkdir(exist_ok=True)
    (documents / 'コード全文.md').write_text('\n'.join(body) + '\n', encoding='utf-8', newline='\n')
    output = root / 'output' / 'deliverables'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'jitsi-ai-source-{selected.date}.zip'
    # Only these public source/document paths enter the archive; runtime folders are never traversed.
    exported = files + assets + list(documents.glob('*.md')) + [root / 'README.md', root / 'THIRD_PARTY.md',
                                                              root / 'assets' / 'announcements' / 'README.md']
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in exported:
            bundle.write(path, path.relative_to(root).as_posix())
    print(f'COMPLETE_SOURCE_FILES={len(files)}')
    print(f'ARCHIVE={archive}')
    print(f'SHA256={hashlib.sha256(archive.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
