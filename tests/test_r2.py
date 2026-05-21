"""R2 uploader tests — SigV4 correctness + config + key mapping.

The signature is validated against the AWS `sig-v4-test-suite` `get-vanilla`
vector (the canonical reference case), so we know the signing math is right
without needing live R2 credentials.
"""
from __future__ import annotations

import hashlib

from ndrchst.runtime import r2


def test_sigv4_matches_aws_s3_get_object_example():
    # AWS S3 SigV4 docs — "Example: GET Object" (the canonical reference
    # case for service=s3). Fully published intermediates + signature.
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert empty == hashlib.sha256(b"").hexdigest()
    auth, signed = r2.sign_v4(
        method="GET",
        canonical_uri="/test.txt",
        query="",
        headers={
            "host": "examplebucket.s3.amazonaws.com",
            "range": "bytes=0-9",
            "x-amz-content-sha256": empty,
            "x-amz-date": "20130524T000000Z",
        },
        payload_hash=empty,
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="s3",
        amz_date="20130524T000000Z",
    )
    assert signed == "host;range;x-amz-content-sha256;x-amz-date"
    assert auth == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_config_from_env_requires_all(monkeypatch):
    for k in ("NDRCHST_R2_ACCOUNT_ID", "NDRCHST_R2_ACCESS_KEY_ID",
              "NDRCHST_R2_SECRET_ACCESS_KEY", "NDRCHST_R2_BUCKET", "NDRCHST_R2_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    assert r2.config_from_env() is None
    monkeypatch.setenv("NDRCHST_R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("NDRCHST_R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("NDRCHST_R2_SECRET_ACCESS_KEY", "sk")
    assert r2.config_from_env() is None  # still missing bucket
    monkeypatch.setenv("NDRCHST_R2_BUCKET", "ndrchst-dl")
    monkeypatch.setenv("NDRCHST_R2_PREFIX", "client")
    cfg = r2.config_from_env()
    assert cfg is not None
    assert cfg.host == "acct.r2.cloudflarestorage.com"
    assert cfg.key("b757b2ea9cea/config.json") == "client/b757b2ea9cea/config.json"


def test_key_no_prefix():
    cfg = r2.R2Config("a", "k", "s", "bucket", prefix="")
    assert cfg.key("/index.html") == "index.html"


def test_put_object_signs_and_sends(monkeypatch):
    import httpx

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["sha"] = request.headers.get("x-amz-content-sha256", "")
        captured["ct"] = request.headers.get("content-type", "")
        captured["cc"] = request.headers.get("cache-control", "")
        captured["body"] = request.content
        return httpx.Response(200)

    cfg = r2.R2Config("acct", "AKID", "secret", "ndrchst-dl", prefix="client")
    body = b'{"hello":"world"}'
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r2.put_object(cfg, "x/latest.json", body, content_type="application/json",
                      cache_control="no-cache", client=client)
    assert captured["url"] == "https://acct.r2.cloudflarestorage.com/ndrchst-dl/client/x/latest.json"
    assert captured["sha"] == hashlib.sha256(body).hexdigest()
    assert captured["ct"] == "application/json"
    assert captured["cc"] == "no-cache"
    assert captured["body"] == body
    assert captured["auth"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
    assert "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date" in captured["auth"]
