"""Minimal Cloudflare R2 (S3-compatible) uploader — AWS SigV4 over httpx,
no boto3 dependency.

Used to push client artifacts off the residential box onto Cloudflare's
edge so distribution scales and the box stays outbound-only. R2 speaks
the S3 API; we sign PUTs with SigV4 (region "auto", service "s3").

Config comes from the environment; when any piece is missing,
`config_from_env()` returns None and callers skip publishing (no-op).
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

_ALGO = "AWS4-HMAC-SHA256"
_REGION = "auto"
_SERVICE = "s3"

# Bounded connect, generous read, no write cap: a large object (e.g. a ~190 MB
# modpack) uploaded over a slow/contended residential uplink progresses for many
# minutes; httpx's default per-chunk write timeout would trip mid-upload.
_UPLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=None, pool=30.0)


@dataclass(frozen=True, slots=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    prefix: str = ""  # optional key prefix, e.g. "client"

    @property
    def host(self) -> str:
        return f"{self.account_id}.r2.cloudflarestorage.com"

    def key(self, path: str) -> str:
        p = path.lstrip("/")
        return f"{self.prefix.strip('/')}/{p}" if self.prefix else p


def config_from_env() -> R2Config | None:
    aid = os.environ.get("NDRCHST_R2_ACCOUNT_ID")
    ak = os.environ.get("NDRCHST_R2_ACCESS_KEY_ID")
    sk = os.environ.get("NDRCHST_R2_SECRET_ACCESS_KEY")
    bucket = os.environ.get("NDRCHST_R2_BUCKET")
    if not (aid and ak and sk and bucket):
        return None
    return R2Config(aid, ak, sk, bucket, os.environ.get("NDRCHST_R2_PREFIX", "").strip("/"))


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str) -> bytes:
    k = _hmac(("AWS4" + secret).encode("utf-8"), date_stamp)
    k = _hmac(k, _REGION)
    k = _hmac(k, _SERVICE)
    return _hmac(k, "aws4_request")


def sign_v4(
    *,
    method: str,
    canonical_uri: str,
    query: str,
    headers: dict[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    amz_date: str,
) -> tuple[str, str]:
    """Core SigV4. `headers` keys must be lowercase. Returns
    (authorization_header_value, signed_headers). Kept generic (region /
    service args) so it can be checked against the AWS sig-v4-test-suite
    `get-vanilla` vector — see tests/test_r2.py."""
    names = sorted(headers)
    canonical_headers = "".join(f"{h}:{headers[h].strip()}\n" for h in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join([
        method, canonical_uri, query, canonical_headers, signed_headers, payload_hash,
    ])
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        _ALGO, amz_date, scope, _sha256_hex(canonical_request.encode("utf-8")),
    ])
    if region == _REGION and service == _SERVICE:
        key = _signing_key(secret_key, date_stamp)
    else:
        # Generic path (used by the test vector with a different region/service).
        k = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
        k = _hmac(k, region)
        k = _hmac(k, service)
        key = _hmac(k, "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = (
        f"{_ALGO} Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return auth, signed_headers


def _encode_path(segments: list[str]) -> str:
    # S3 canonical URI: encode each segment, keep the slashes.
    return "/" + "/".join(
        urllib.parse.quote(s, safe="-_.~") for s in segments
    )


def put_object(
    cfg: R2Config,
    path: str,
    body: bytes,
    *,
    content_type: str = "application/octet-stream",
    cache_control: str | None = None,
    client: httpx.Client | None = None,
) -> None:
    """PUT one object to R2 at <prefix>/<path>. Raises on non-2xx."""
    key = cfg.key(path)
    canonical_uri = _encode_path([cfg.bucket, *key.split("/")])
    amz_date = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    payload_hash = _sha256_hex(body)
    sign = {
        "host": cfg.host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "content-type": content_type,
    }
    auth, _ = sign_v4(
        method="PUT", canonical_uri=canonical_uri, query="", headers=sign,
        payload_hash=payload_hash, access_key=cfg.access_key_id,
        secret_key=cfg.secret_access_key, region=_REGION, service=_SERVICE,
        amz_date=amz_date,
    )
    send = {
        "Host": cfg.host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Content-Type": content_type,
        "Authorization": auth,
    }
    if cache_control:
        send["Cache-Control"] = cache_control  # unsigned — S3 allows it
    url = f"https://{cfg.host}{canonical_uri}"
    owns = client is None
    c = client or httpx.Client(timeout=_UPLOAD_TIMEOUT)
    try:
        r = c.put(url, content=body, headers=send)
        r.raise_for_status()
    finally:
        if owns:
            c.close()


def _canonical_query(params: dict[str, str]) -> str:
    """SigV4 canonical query string: params sorted by name, both key and value
    percent-encoded down to the RFC-3986 unreserved set, joined by '&'. httpx
    transmits an already-encoded query string byte-for-byte (verified), so what
    we sign here is exactly what goes on the wire."""
    return "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(params.items())
    )


def _signed_get_or_delete(
    cfg: R2Config,
    *,
    method: str,
    canonical_uri: str,
    query: str,
    client: httpx.Client,
) -> httpx.Response:
    """Sign + send an empty-body request (GET list / DELETE object). Shared by
    list_objects and delete_object — same SigV4 shape, empty payload hash."""
    amz_date = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    empty_hash = _sha256_hex(b"")
    sign = {
        "host": cfg.host,
        "x-amz-content-sha256": empty_hash,
        "x-amz-date": amz_date,
    }
    auth, _ = sign_v4(
        method=method, canonical_uri=canonical_uri, query=query, headers=sign,
        payload_hash=empty_hash, access_key=cfg.access_key_id,
        secret_key=cfg.secret_access_key, region=_REGION, service=_SERVICE,
        amz_date=amz_date,
    )
    send = {
        "Host": cfg.host,
        "x-amz-content-sha256": empty_hash,
        "x-amz-date": amz_date,
        "Authorization": auth,
    }
    url = f"https://{cfg.host}{canonical_uri}"
    if query:
        url += f"?{query}"
    r = client.request(method, url, headers=send)
    r.raise_for_status()
    return r


def _parse_list_v2(xml_bytes: bytes) -> tuple[list[tuple[str, int]], str]:
    """Parse a ListObjectsV2 XML body into ([(key, size), ...], next_token).
    next_token is "" when the listing isn't truncated. Tolerates the S3
    namespace by matching on the local tag name."""
    # R2's own ListObjectsV2 response — a trusted source, not user input.
    root = ET.fromstring(xml_bytes)

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    keys: list[tuple[str, int]] = []
    token = ""
    for el in root:
        name = local(el.tag)
        if name == "Contents":
            key = None
            size = 0
            for ch in el:
                cn = local(ch.tag)
                if cn == "Key":
                    key = ch.text
                elif cn == "Size":
                    size = int(ch.text or 0)
            if key is not None:
                keys.append((key, size))
        elif name == "NextContinuationToken":
            token = el.text or ""
    return keys, token


def list_objects(
    cfg: R2Config, prefix: str = "", *, client: httpx.Client | None = None
) -> list[tuple[str, int]]:
    """List every object key under `prefix` (paginating past 1000), returning
    [(key, size_bytes), ...]. `prefix` is a RAW bucket prefix (e.g. 'pilot/');
    cfg.prefix is honored if set. Read-only — used by the orphan-purge tool."""
    full_prefix = cfg.key(prefix) if (cfg.prefix and prefix) else prefix
    canonical_uri = _encode_path([cfg.bucket])
    out: list[tuple[str, int]] = []
    token = ""
    owns = client is None
    c = client or httpx.Client(timeout=_UPLOAD_TIMEOUT)
    try:
        while True:
            params = {"list-type": "2"}
            if full_prefix:
                params["prefix"] = full_prefix
            if token:
                params["continuation-token"] = token
            query = _canonical_query(params)
            r = _signed_get_or_delete(
                cfg, method="GET", canonical_uri=canonical_uri, query=query, client=c)
            page, token = _parse_list_v2(r.content)
            out.extend(page)
            if not token:
                break
    finally:
        if owns:
            c.close()
    return out


def delete_object(
    cfg: R2Config, key: str, *, client: httpx.Client | None = None
) -> None:
    """DELETE one RAW object key (as returned by list_objects — NOT re-prefixed).
    R2 returns 204 even for a missing key, so this is idempotent. Raises on a
    non-2xx (auth / network) so the caller can stop on a real failure."""
    canonical_uri = _encode_path([cfg.bucket, *key.split("/")])
    owns = client is None
    c = client or httpx.Client(timeout=_UPLOAD_TIMEOUT)
    try:
        _signed_get_or_delete(
            cfg, method="DELETE", canonical_uri=canonical_uri, query="", client=c)
    finally:
        if owns:
            c.close()
