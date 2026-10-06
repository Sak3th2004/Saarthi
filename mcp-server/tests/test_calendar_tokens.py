"""Use synthetic tokens only; never touch the configured calendar token file."""

from pathlib import Path
import sys
import traceback

import pytest

from saarthi_mcp import calendar_tokens as tokens


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows DPAPI")


def test_missing_store_has_no_credentials(tmp_path):
    assert tokens.LocalTokenStore(tmp_path / "missing" / "tokens.bin").load() is None
    assert list(tmp_path.iterdir()) == []


@windows_only
def test_roundtrip_is_encrypted_and_replacement_persists(tmp_path):
    path = tmp_path / "private" / "tokens.bin"
    store = tokens.LocalTokenStore(path)
    payload = {"refresh_token": "synthetic-refresh-marker-4823", "token": "synthetic-access-marker", "scopes": ["calendar"], "expiry": None}
    store.save(payload)
    raw = path.read_bytes()
    assert payload["refresh_token"].encode() not in raw
    assert payload["token"].encode() not in raw
    assert tokens.LocalTokenStore(path).load() == payload
    store.save({"refresh_token": "replacement"})
    assert store.load() == {"refresh_token": "replacement"}
    assert list(path.parent.iterdir()) == [path]


@windows_only
def test_failed_replace_preserves_saved_credentials_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / "tokens.bin"
    store = tokens.LocalTokenStore(path)
    store.save({"token": "original"})
    original = path.read_bytes()

    def fail_replace(source, destination):
        assert Path(source).parent == path.parent
        assert b"replacement-sensitive-marker" not in Path(source).read_bytes()
        raise PermissionError("private-path-sensitive-marker")

    monkeypatch.setattr(tokens.os, "replace", fail_replace)
    replacement = {"token": "replacement-sensitive-marker"}
    with pytest.raises(tokens.TokenStoreError) as exc:
        store.save(replacement)
    assert "sensitive-marker" not in "".join(traceback.format_exception(exc.value))
    assert path.read_bytes() == original
    assert store.load() == {"token": "original"}
    assert list(tmp_path.iterdir()) == [path]


@windows_only
@pytest.mark.parametrize("data", [b"", b"sensitive-corrupt-marker", tokens._HEADER + b"sensitive-corrupt-marker"])
def test_corruption_has_sanitized_error(tmp_path, data):
    path = tmp_path / "tokens.bin"
    path.write_bytes(data)
    with pytest.raises(tokens.TokenStoreError) as exc:
        tokens.LocalTokenStore(path).load()
    assert "sensitive-corrupt-marker" not in "".join(traceback.format_exception(exc.value))


@windows_only
def test_oversized_file_is_rejected_without_decryption(tmp_path, monkeypatch):
    path = tmp_path / "tokens.bin"
    path.write_bytes(tokens._HEADER + b"a" * tokens._MAX_FILE_BYTES)

    def unexpected(*args, **kwargs):
        pytest.fail("Oversized input must not reach DPAPI")

    monkeypatch.setattr(tokens, "_crypt", unexpected)
    with pytest.raises(tokens.TokenStoreError):
        tokens.LocalTokenStore(path).load()


@windows_only
def test_invalid_or_oversized_payload_does_not_overwrite_saved_token(tmp_path):
    store = tokens.LocalTokenStore(tmp_path / "tokens.bin")
    store.save({"token": "original"})
    for payload in [{"token": "x" * tokens._MAX_JSON_BYTES}, {"bad": float("nan")}, ["not-an-object"]]:
        with pytest.raises(tokens.TokenStoreError):
            store.save(payload)
        assert store.load() == {"token": "original"}


def test_other_platforms_fail_closed_for_existing_file_and_save(tmp_path, monkeypatch):
    path = tmp_path / "tokens.bin"
    path.write_bytes(b"existing-file")
    monkeypatch.setattr(tokens.sys, "platform", "linux")
    store = tokens.LocalTokenStore(path)
    with pytest.raises(tokens.TokenStoreError, match="requires Windows DPAPI"):
        store.load()
    with pytest.raises(tokens.TokenStoreError, match="requires Windows DPAPI"):
        store.save({"token": "no-plaintext-fallback"})
    assert path.read_bytes() == b"existing-file"
    assert tokens.LocalTokenStore(tmp_path / "missing.bin").load() is None
