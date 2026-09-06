"""Explicit first-run account setup, separate from routine MCP collection."""

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path

from .collectors import load_sources
from .kakao_db import device_uuid, derive_key, local_metadata, query_database, sqlcipher_path


def recovery_binary(data_dir):
    source = Path(__file__).with_name('kakao_account_recovery.c')
    cache = data_dir / 'bin'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache.chmod(0o700)
    binary = cache / ('account-setup-' + platform.machine() + '-' + hashlib.sha256(source.read_bytes()).hexdigest()[:16])
    if not binary.is_file():
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            output = Path(temporary) / 'helper'
            try:
                result = subprocess.run(['/usr/bin/clang', '-O3', '-std=c11', str(source), '-lpthread', '-o', str(output)], capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                raise RuntimeError('Setup helper compilation timed out; check Xcode Command Line Tools and retry') from None
            if result.returncode:
                raise RuntimeError('Setup helper build failed. Install Xcode Command Line Tools with xcode-select --install, then retry')
            output.replace(binary)
    return binary


def recover_id(account_hash, data_dir, *, maximum=1_000_000_000, timeout=300):
    if not re.fullmatch('[0-9a-f]{128}', account_hash):
        raise ValueError('Invalid local account hash')
    binary = recovery_binary(data_dir)
    lanes = min(os.cpu_count() or 1, 8)
    try:
        result = subprocess.run([str(binary)], input=f'{account_hash} {maximum} {lanes}\n', capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Account setup reached its 300-second search limit; no configuration was changed') from None
    if result.returncode or not result.stdout.strip().isdigit():
        raise RuntimeError('Account ID was not resolved within the supported local range; no configuration was changed')
    user_id = int(result.stdout)
    if not 1 <= user_id <= maximum or hashlib.sha512(str(user_id).encode()).hexdigest() != account_hash:
        raise RuntimeError('Account setup verification failed')
    return user_id


def write_private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.setup-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def setup_account(config, data_dir, source_id, progress=lambda message: None):
    if platform.system() != 'Darwin':
        raise RuntimeError('Kakao account setup requires macOS')
    sqlcipher_path()
    original_config = config.read_bytes()
    sources = load_sources(config)
    source = next((s for s in sources if s['id'] == source_id), None)
    if not source or source['kind'] != 'kakao_db':
        raise ValueError('Choose an existing kakao_db source ID')
    existing = source.get('account_file')
    if existing and not Path(existing).is_file():
        existing = None
    databases, candidates, hashes = local_metadata(existing)
    # Empty-account markers are not a logged-in account and must not trigger work.
    hashes = {h for h in hashes if re.fullmatch('[0-9a-f]{128}', h) and h != hashlib.sha512(b'0').hexdigest()}
    if len(hashes) != 1:
        raise RuntimeError('Active account is missing or ambiguous. Sign in to KakaoTalk with the intended account and retry')
    account_hash = next(iter(hashes))
    candidates = {uid for uid in candidates if hashlib.sha512(str(uid).encode()).hexdigest() == account_hash}
    if candidates:
        user_id = next(iter(candidates))
        progress('기존 로컬 계정 정보를 검증합니다.')
    else:
        progress('최초 계정 ID를 로컬에서 확인합니다. 최대 5분간 CPU를 사용할 수 있습니다.')
        user_id = recover_id(account_hash, data_dir)
    uuid = device_uuid()
    key = derive_key(user_id, uuid)
    verified = False
    for database in databases[:8]:
        try:
            query_database(database, key, 'SELECT count(*) AS n FROM sqlite_master', timeout=3)
            verified = True
            break
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
    if not verified:
        raise RuntimeError('Account candidate could not open the local DB; no configuration was changed')
    _, _, current_hashes = local_metadata()
    current_hashes = {h for h in current_hashes if re.fullmatch('[0-9a-f]{128}', h) and h != hashlib.sha512(b'0').hexdigest()}
    if current_hashes != hashes:
        raise RuntimeError('Active account changed during setup; retry')
    if config.read_bytes() != original_config:
        raise RuntimeError('Source configuration changed during setup; retry without overwriting it')
    account_file = (data_dir / f'{source_id}-account.json').resolve()
    write_private_json(account_file, {'user_id': user_id})
    source['account_file'] = str(account_file)
    write_private_json(config, sources)
    return {'status': 'ok', 'source_id': source_id, 'account_file': str(account_file),
            'message': 'Account verified against the local DB. Set the exact room_title and enable the source before collecting.'}
