"""18.1절: Credential 암호화 저장.

"Credential은 암호화 저장하고 UI/API에서 평문으로 재노출하지 않는다"는 원칙에
따라, Fernet(대칭키) 암호화로 Credential 문자열을 저장/복호화한다.

키는 배포 단순성(15.1절, 단일 파일 nms.db)에 맞춰 별도 KMS 없이 데이터 디렉터리
안의 파일(secret.key)로 관리한다. 파일 권한을 0600으로 제한해 동일 서버의 다른
사용자로부터 최소한의 보호를 제공하지만, DB 파일과 키 파일 모두에 접근 가능한
공격자(예: root, 파일시스템 전체 백업 탈취)를 막지는 못한다는 한계가 있다 —
운영 환경에서는 외부 KMS/Vault 연동으로 대체하는 것을 권장한다.
"""
from __future__ import annotations

import stat
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app import config

SECRET_KEY_PATH = config.DATA_DIR / "secret.key"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    if not SECRET_KEY_PATH.exists():
        key = Fernet.generate_key()
        SECRET_KEY_PATH.write_bytes(key)
        SECRET_KEY_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    else:
        key = SECRET_KEY_PATH.read_bytes()
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Credential 복호화 실패: secret.key가 변경되었거나 손상된 데이터입니다.") from exc
