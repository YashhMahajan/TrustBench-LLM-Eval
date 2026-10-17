import pytest

from trustbench import crypto
from trustbench.dataprotect import DataPolicy, PolicyProblem, protect, update_policy
from trustbench.redact import RedactionError, redact
from trustbench.tenancy import create_workspace


def clean(text, custom=None):
    return redact(text, custom)


@pytest.mark.parametrize("text,marker", [
    ("Write to ana.silva+test@gmail.com today", "[EMAIL]"),
    ("My SSN is 123-45-6789.", "[SSN]"),
    ("Call +1 415-555-0134 now", "[PHONE]"),
    ("Call (415) 555 0134 now", "[PHONE]"),
    ("Card 4111 1111 1111 1111 please", "[CARD_NUMBER]"),
    ("Server at 192.168.10.25 is down", "[IP_ADDRESS]"),
    ("key sk-abcdefghijklmnop1234567890", "[SECRET]"),
    ("aws AKIAIOSFODNN7EXAMPLE here", "[SECRET]"),
    ("token ghp_" + "a" * 36, "[SECRET]"),
    ("Authorization: Bearer abcdef1234567890abcdef", "[SECRET]"),
    ("password = hunter2hunter2", "[SECRET]"),
    ("api_key: \"abc123def456\"", "[SECRET]"),
    ("db at postgres://admin:s3cret@db.internal/app", "[URL_WITH_LOGIN]"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r", "[JWT]"),
    ("pay to GB82 WEST 1234 5698 7654 32 soon", "[IBAN]"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "[PRIVATE_KEY]"),
])
def test_personal_data_and_secrets_are_replaced(text, marker):
    out, counts = clean(text)
    assert marker in out and sum(counts.values()) >= 1
    for secret in ("gmail.com", "6789", "555-0134", "4111", "192.168", "hunter2", "s3cret", "AKIAIOSFODNN7", "sk-abc", "abcdef1234567890"):
        assert secret not in out or secret not in text


def test_ordinary_text_is_left_alone():
    for text in ["The shop opens at 9 am and closes at 5 pm.", "Order 12345 shipped on 2024-03-05 for 49.90 euros.",
                 "Chapter 3, page 120 of 400. Version 1.2.3 is out.", "Call us between 9 and 17 hours.",
                 "Invoice number 4111 1111 1111 1112 is not a card."]:
        out, counts = clean(text)
        assert out == text and counts == {}, text


def test_counts_are_kept_but_values_are_not():
    out, counts = clean("a@b.com and c@d.org and 123-45-6789")
    assert counts == {"EMAIL": 2, "SSN": 1} and "@" not in out


def test_redaction_is_stable_when_applied_twice():
    once, _ = clean("mail ana@gmail.com, card 4111111111111111, key sk-abcdefghijklmnop1234")
    twice, counts = clean(once)
    assert once == twice and counts == {}


def test_custom_patterns():
    out, counts = clean("Customer C-00123 asked", ["C-\\d{5}"])
    assert out == "Customer [CUSTOM] asked" and counts == {"CUSTOM": 1}
    with pytest.raises(RedactionError, match="not a valid"):
        clean("x", ["(unclosed"])
    with pytest.raises(RedactionError, match="longer than 200"):
        clean("x", ["a" * 201])


@pytest.fixture
def key(monkeypatch):
    k = crypto.generate_key()
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", k)
    return k


def test_encryption_round_trip_and_no_plain_text(key):
    token = crypto.encrypt_text("secret answer: 42")
    assert token.startswith("v1:") and "42" not in token and crypto.decrypt_text(token) == "secret answer: 42"
    assert crypto.encrypt_text("same") != crypto.encrypt_text("same")      # A fresh nonce each time.


def test_wrong_key_and_damaged_content_fail_clearly(key, monkeypatch):
    token = crypto.encrypt_text("x")
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    with pytest.raises(crypto.DecryptionError, match="cannot be decrypted"):
        crypto.decrypt_text(token)
    with pytest.raises(crypto.DecryptionError, match="Unknown format"):
        crypto.decrypt_text("plain")


def test_key_rotation(key, monkeypatch):
    old_token = crypto.encrypt_text("old data")
    new = crypto.generate_key()
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", f"{new},{key}")          # New key first.
    assert crypto.decrypt_text(old_token) == "old data"
    fresh = crypto.encrypt_text("new data")
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", new)
    assert crypto.decrypt_text(fresh) == "new data"


def test_missing_or_bad_key(monkeypatch):
    monkeypatch.delenv("TRUSTBENCH_SECRET_KEY", raising=False)
    assert not crypto.available()
    with pytest.raises(crypto.SecretKeyMissing, match="generate-key"):
        crypto.encrypt_text("x")
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", "not-a-key")
    with pytest.raises(crypto.SecretKeyMissing, match="not a valid key"):
        crypto.encrypt_text("x")


def test_default_policy_stores_no_content():
    p = protect(DataPolicy(), "my email is ana@gmail.com")
    assert p.text is None and p.ciphertext is None and p.length == 25 and p.redactions == {"EMAIL": 1}
    assert protect(DataPolicy(), "my email is ana@gmail.com").content_hash == protect(DataPolicy(), "my email is bob@gmail.com").content_hash


def test_stored_content_is_redacted_then_encrypted(key):
    p = protect(DataPolicy(store_content=True), "call me: ana@gmail.com about the refund")
    assert p.ciphertext and "gmail" not in p.ciphertext
    assert crypto.decrypt_text(p.ciphertext) == "call me: [EMAIL] about the refund"


def test_redaction_can_be_switched_off_and_text_is_cut(key):
    p = protect(DataPolicy(store_content=True, redact=False, max_payload_chars=100), "ana@gmail.com " + "x" * 200)
    assert p.truncated and p.redactions == {} and crypto.decrypt_text(p.ciphertext).startswith("ana@gmail.com")
    assert len(crypto.decrypt_text(p.ciphertext)) == 100 and p.length == 214


def test_policy_values_are_checked():
    for bad in ({"retention_days": 0}, {"sample_rate": 1.5}, {"extra_redactions": ["("]}, {"unknown": 1}):
        with pytest.raises(Exception):
            DataPolicy(**bad)


def test_update_policy_is_all_or_nothing_and_needs_a_key_for_content(factory, monkeypatch):
    ws = create_workspace(factory, "acme")
    monkeypatch.delenv("TRUSTBENCH_SECRET_KEY", raising=False)
    with pytest.raises(PolicyProblem, match="encryption key"):
        update_policy(factory, ws.id, store_content=True)
    with pytest.raises(PolicyProblem, match="sample_rate"):
        update_policy(factory, ws.id, retention_days=7, sample_rate=2)
    assert update_policy(factory, ws.id, retention_days=7).retention_days == 7     # The failed call changed nothing before.
    monkeypatch.setenv("TRUSTBENCH_SECRET_KEY", crypto.generate_key())
    p = update_policy(factory, ws.id, store_content=True)
    assert p.store_content and p.retention_days == 7
    with pytest.raises(PolicyProblem, match="No workspace"):
        update_policy(factory, "nope", retention_days=3)
