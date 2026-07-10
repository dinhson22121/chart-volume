from app import crypto


def test_encrypt_decrypt_roundtrip():
    ct = crypto.encrypt("sk-ant-secret-value")
    assert ct != "sk-ant-secret-value"
    assert ct.startswith("enc:")
    assert crypto.decrypt(ct) == "sk-ant-secret-value"


def test_encrypt_empty_string_is_empty():
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""


def test_decrypt_tolerates_plaintext_legacy_value():
    # A value stored before encryption was introduced (no "enc:" prefix).
    assert crypto.decrypt("plain-legacy-key") == "plain-legacy-key"


def test_decrypt_invalid_token_returns_empty():
    assert crypto.decrypt("enc:not-a-real-token") == ""
