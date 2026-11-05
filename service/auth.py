# File: service/auth.py
# Summary: Issue expiring administrator sessions and revoke them on sign-out without retaining plaintext tokens.

import hashlib
import secrets
import time

from . import storage as store

COOKIE = "jitsi_ai_admin"
SESSION_SECONDS = 8 * 60 * 60


def credential_hash(config):
    """管理者のパスワード設定が変更された場合に、既存セッションを無効と判定する。"""
    return store.digest(config.get("admin_salt", "") + ":" + config.get("admin_digest", ""))


def sign_in(username, password, previous_token=""):
    """既存のパスワードハッシュを検証し、このブラウザーのセッションを新しい乱数へ交換する。"""
    config = store.settings()
    salt = config.get("admin_salt", "")
    calculated = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex()
    if (not salt or not secrets.compare_digest(username.encode(), b"ubuntu")
            or not secrets.compare_digest(calculated, config.get("admin_digest", ""))):
        return None
    token = secrets.token_urlsafe(32)
    now = time.time()
    marker = credential_hash(config)
    with store.db() as conn:
        # 期限切れ・パスワード変更前の記録と、交換前のセッションを削除する。
        conn.execute("DELETE FROM admin_sessions WHERE expires<=? OR credential_hash<>?", (now, marker))
        conn.execute("DELETE FROM admin_sessions WHERE hash=?", (store.digest(previous_token),))
        conn.execute("INSERT INTO admin_sessions(hash,expires,credential_hash) VALUES (?,?,?)",
                     (store.digest(token), now + SESSION_SECONDS, marker))
    return token


def authenticated(token):
    """Cookieのハッシュを照合し、有効期限と現在の管理者設定が一致する場合だけ許可する。"""
    if not token or len(token) > 128:
        return False
    with store.db() as conn:
        row = conn.execute("SELECT expires,credential_hash FROM admin_sessions WHERE hash=?",
                           (store.digest(token),)).fetchone()
        if row and (row["expires"] <= time.time()
                    or not secrets.compare_digest(row["credential_hash"], credential_hash(store.settings()))):
            conn.execute("DELETE FROM admin_sessions WHERE hash=?", (store.digest(token),))
            return False
    return row is not None


def sign_out(token):
    """現在のセッションだけを削除し、Cookieを再送されても再利用できない状態にする。"""
    if token:
        with store.db() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE hash=?", (store.digest(token),))
