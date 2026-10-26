"""An AWS Secrets Manager compatible server (Python, standard library only).

boto3, the AWS SDKs (JavaScript v3, Java, Go, ...), the AWS CLI and pskit's ``get_secret()`` talk to it unchanged:
point their ``endpoint_url`` at this server and sign with an access key / secret key it knows. Requests are checked
with AWS Signature Version 4, exactly as AWS does, so a wrong secret key is refused.

A web console (``/ui/``) lists, creates, edits, versions, deletes and restores secrets. It signs in with the same
access key and secret key (or ``SM_UI_PASSWORD``), keeps the session in an HttpOnly cookie and never sends the keys
again.

Settings (environment):

``SM_ACCESS_KEY_ID`` / ``SM_SECRET_ACCESS_KEY``
    The credentials clients sign with (``aws_access_key_id`` / ``aws_secret_access_key``).
``SM_CREDENTIALS``
    More credentials: ``id1:secret1,id2:secret2``.
``SM_AUTH``
    ``sigv4`` (default) or ``off`` (no authentication: development only).
``SM_PORT`` (4584), ``SM_BIND`` (0.0.0.0), ``SM_DATA_DIR`` (./data), ``SM_REGION`` (us-east-1),
``SM_ACCOUNT_ID`` (000000000000)
    Where it listens, where the secrets file lives, and the region / account in ARNs.
``SM_UI`` (``on``), ``SM_UI_PASSWORD``
    The web console, and a console password other than the secret key (sign in with any access key id then).

The data is one JSON file (``secretsmanager.json``, mode 600) written atomically. The Node.js server
(``../nodejs/server.js``) reads and writes the same file, so you can switch between them.

``python server.py --check`` exits 0 when the server answers on ``/health`` (a container health check).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import string
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlsplit

VERSION = "1.0.0"
ENV = os.environ
PORT = int(ENV.get("SM_PORT") or 4584)
BIND = ENV.get("SM_BIND") or "0.0.0.0"  # noqa: S104 - a server; set SM_BIND=127.0.0.1 to keep it local
DATA_DIR = Path(ENV.get("SM_DATA_DIR") or Path.cwd() / "data")
STORE_FILE = DATA_DIR / "secretsmanager.json"
REGION = ENV.get("SM_REGION") or "us-east-1"
ACCOUNT = ENV.get("SM_ACCOUNT_ID") or "000000000000"
AUTH = (ENV.get("SM_AUTH") or "sigv4").lower()
UI_ON = (ENV.get("SM_UI") or "on").lower() not in ("off", "0", "false", "no")
UI_PASSWORD = ENV.get("SM_UI_PASSWORD") or ""
UI_FILE = ENV.get("SM_UI_FILE") or ""
MAX_BODY = 1024 * 1024
MAX_VALUE = 65536
MAX_VERSIONS = 100
SKEW = 15 * 60
SESSION_TTL = 12 * 3600
PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_NAME = re.compile(r"^[A-Za-z0-9/_+=.@-]{1,512}$")
_AUTH = re.compile(r"^AWS4-HMAC-SHA256\s+Credential=([^,\s]+),\s*SignedHeaders=([^,\s]+),\s*Signature=([0-9a-f]{64})$")


def _credentials() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if ENV.get("SM_SECRET_ACCESS_KEY"):
        out[ENV.get("SM_ACCESS_KEY_ID") or "pskit"] = ENV["SM_SECRET_ACCESS_KEY"]
    for pair in (ENV.get("SM_CREDENTIALS") or "").split(","):
        key, sep, secret = pair.strip().partition(":")
        if sep and key and secret:
            out[key] = secret
    return out


CREDENTIALS = _credentials()


def log(*parts: Any) -> None:
    print(datetime.now(timezone.utc).isoformat(timespec="seconds"), *parts, flush=True)


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _not_found(what: str = "secret") -> ApiError:
    return ApiError("ResourceNotFoundException", f"Secrets Manager can't find the specified {what}.")


def _invalid(message: str) -> ApiError:
    return ApiError("InvalidParameterException", message)


def _request(message: str) -> ApiError:
    return ApiError("InvalidRequestException", message)


def _now() -> float:
    return round(time.time(), 3)


def _today() -> float:
    return float(int(time.time()) // 86400 * 86400)


# ----------------------------------------------------------------------------- store


class Store:
    """Every secret, in memory and in one JSON file. Callers hold ``lock``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.secrets: Dict[str, Dict[str, Any]] = {}
        if path.is_file():
            data = json.loads(path.read_text("utf-8") or "{}")
            self.secrets = {s["Name"]: s for s in data.get("secrets") or []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {"format": 1, "secrets": [self.secrets[n] for n in sorted(self.secrets)]}
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(payload, indent=1, sort_keys=True).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(self.path))

    def purge(self) -> None:
        now = time.time()
        gone = [n for n, s in self.secrets.items() if s.get("DeletionDate") and s["DeletionDate"] <= now]
        for name in gone:
            del self.secrets[name]
            log("purged", name)
        if gone:
            self.save()

    def find(self, secret_id: Any) -> Dict[str, Any]:
        if not isinstance(secret_id, str) or not secret_id:
            raise _invalid("SecretId is required.")
        if secret_id in self.secrets:
            return self.secrets[secret_id]
        for secret in self.secrets.values():
            arn = secret["ARN"]
            # A full ARN, or a partial one without the 6-character suffix AWS adds to the name.
            if secret_id == arn or (len(arn) == len(secret_id) + 7 and arn.startswith(secret_id + "-")):
                return secret
        raise _not_found()

    def live(self, secret_id: Any) -> Dict[str, Any]:
        secret = self.find(secret_id)
        if secret.get("DeletedDate"):
            raise _request("You can't perform this operation on the secret because it was marked for deletion.")
        return secret


STORE: Store


# ----------------------------------------------------------------------------- helpers


def _token(params: Dict[str, Any]) -> str:
    token = params.get("ClientRequestToken")
    if token is None:
        return str(uuid.uuid4())
    if not isinstance(token, str) or not 32 <= len(token) <= 64:
        raise _invalid("ClientRequestToken must be 32-64 characters.")
    return token


def _value(params: Dict[str, Any], required: bool = False) -> Optional[Dict[str, str]]:
    text, blob = params.get("SecretString"), params.get("SecretBinary")
    if text is not None and blob is not None:
        raise _invalid("You can't specify both a binary secret value and a string secret value in the same secret.")
    if text is not None:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_VALUE:
            raise _invalid(f"SecretString must be a string of at most {MAX_VALUE} bytes.")
        return {"SecretString": text}
    if blob is not None:
        try:
            raw = base64.b64decode(str(blob), validate=True)
        except ValueError:
            raise _invalid("SecretBinary must be base64.") from None
        if len(raw) > MAX_VALUE:
            raise _invalid(f"SecretBinary must be at most {MAX_VALUE} bytes.")
        return {"SecretBinary": base64.b64encode(raw).decode("ascii")}
    if required:
        raise _invalid("You must provide either SecretString or SecretBinary.")
    return None


def _tags(raw: Any) -> List[Dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _invalid("Tags must be a list of {Key, Value}.")
    out = []
    for tag in raw:
        if not isinstance(tag, dict) or not isinstance(tag.get("Key"), str) or not 1 <= len(tag["Key"]) <= 128:
            raise _invalid("Each tag needs a Key of 1-128 characters.")
        out.append({"Key": tag["Key"], "Value": str(tag.get("Value") or "")})
    return out


def _merge_tags(old: List[Dict[str, str]], new: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged = {t["Key"]: t["Value"] for t in old}
    merged.update({t["Key"]: t["Value"] for t in new})
    return [{"Key": k, "Value": v} for k, v in merged.items()]


def _stages(secret: Dict[str, Any]) -> Dict[str, List[str]]:
    return {vid: list(v["Stages"]) for vid, v in secret["Versions"].items() if v["Stages"]}


def _same_value(version: Dict[str, Any], value: Dict[str, str]) -> bool:
    return version.get("SecretString") == value.get("SecretString") and \
        version.get("SecretBinary") == value.get("SecretBinary")


def _move_stage(secret: Dict[str, Any], stage: str, to: str) -> None:
    """Attach ``stage`` to version ``to`` only. Moving AWSCURRENT gives the old current version AWSPREVIOUS."""
    versions = secret["Versions"]
    for vid, version in versions.items():
        if vid != to and stage in version["Stages"]:
            version["Stages"].remove(stage)
            if stage == "AWSCURRENT":
                for other in versions.values():
                    if "AWSPREVIOUS" in other["Stages"]:
                        other["Stages"].remove("AWSPREVIOUS")
                version["Stages"].append("AWSPREVIOUS")
    if stage not in versions[to]["Stages"]:
        versions[to]["Stages"].append(stage)


def _prune(secret: Dict[str, Any]) -> None:
    versions = secret["Versions"]
    if len(versions) <= MAX_VERSIONS:
        return
    old = sorted((v["CreatedDate"], vid) for vid, v in versions.items() if not v["Stages"])
    for _created, vid in old[: len(versions) - MAX_VERSIONS]:
        del versions[vid]


def _add_version(secret: Dict[str, Any], token: str, value: Dict[str, str], stages: Optional[List[str]]) -> List[str]:
    """A new version of ``secret`` (idempotent for the same token and value). Returns its stages."""
    if token in secret["Versions"]:
        existing = secret["Versions"][token]
        if not _same_value(existing, value):
            raise ApiError("ResourceExistsException",
                           "You can't modify an existing version, you can only create a new version.")
        return list(existing["Stages"])
    if stages is None:
        stages = ["AWSCURRENT"]
    if not isinstance(stages, list) or not all(isinstance(s, str) and 1 <= len(s) <= 256 for s in stages):
        raise _invalid("VersionStages must be a list of staging labels.")
    secret["Versions"][token] = {**value, "CreatedDate": _now(), "Stages": []}
    for stage in dict.fromkeys(stages):
        _move_stage(secret, stage, token)
    secret["LastChangedDate"] = _now()
    _prune(secret)
    return list(secret["Versions"][token]["Stages"])


def _pick_version(secret: Dict[str, Any], params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    vid, stage = params.get("VersionId"), params.get("VersionStage")
    versions = secret["Versions"]
    if vid:
        version = versions.get(vid)
        if version is None or (stage and stage not in version["Stages"]):
            raise ApiError("ResourceNotFoundException",
                           f"Secrets Manager can't find the specified secret value for VersionId: {vid}")
        return vid, version
    stage = stage or "AWSCURRENT"
    for vid, version in versions.items():
        if stage in version["Stages"]:
            return vid, version
    raise ApiError("ResourceNotFoundException",
                   f"Secrets Manager can't find the specified secret value for staging label: {stage}")


def _value_out(secret: Dict[str, Any], vid: str, version: Dict[str, Any]) -> Dict[str, Any]:
    out = {"ARN": secret["ARN"], "Name": secret["Name"], "VersionId": vid,
           "VersionStages": list(version["Stages"]), "CreatedDate": version["CreatedDate"]}
    for field in ("SecretString", "SecretBinary"):
        if field in version:
            out[field] = version[field]
    return out


def _describe(secret: Dict[str, Any], *, list_shape: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ARN": secret["ARN"], "Name": secret["Name"], "RotationEnabled": False,
                           "CreatedDate": secret["CreatedDate"], "LastChangedDate": secret["LastChangedDate"]}
    for field in ("Description", "KmsKeyId", "LastAccessedDate", "DeletedDate"):
        if secret.get(field) is not None:
            out[field] = secret[field]
    if secret.get("Tags"):
        out["Tags"] = [dict(t) for t in secret["Tags"]]
    out["SecretVersionsToStages" if list_shape else "VersionIdsToStages"] = _stages(secret)
    return out


def _page(params: Dict[str, Any], items: List[Any], limit_default: int = 100) -> Tuple[List[Any], Optional[str]]:
    try:
        limit = int(params.get("MaxResults") or limit_default)
    except (TypeError, ValueError):
        raise _invalid("MaxResults must be a number.") from None
    if not 1 <= limit <= 100:
        raise _invalid("MaxResults must be between 1 and 100.")
    start = 0
    if params.get("NextToken"):
        try:
            start = int(base64.b64decode(str(params["NextToken"])).decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            raise _invalid("NextToken is not valid.") from None
    chunk = items[start:start + limit]
    more = start + limit < len(items)
    return chunk, (base64.b64encode(str(start + limit).encode("ascii")).decode("ascii") if more else None)


def _matches(secret: Dict[str, Any], filters: Any) -> bool:
    """ListSecrets filters: every filter must match; a filter matches when one of its values (a case-insensitive
    prefix, ``!`` in front negates) matches."""
    if not filters:
        return True
    if not isinstance(filters, list):
        raise _invalid("Filters must be a list of {Key, Values}.")
    for flt in filters:
        key = (flt or {}).get("Key")
        values = (flt or {}).get("Values") or []
        tags = secret.get("Tags") or []
        fields = {
            "name": [secret["Name"]],
            "description": [secret.get("Description") or ""],
            "tag-key": [t["Key"] for t in tags],
            "tag-value": [t["Value"] for t in tags],
            "primary-region": [],
            "owning-service": [],
        }
        if key == "all":
            candidates = [c for name in ("name", "description", "tag-key", "tag-value") for c in fields[name]]
        elif key in fields:
            candidates = fields[key]
        else:
            raise _invalid(f"Filter key {key!r} is not valid.")
        hit = False
        for value in values:
            text = str(value)
            negate = text.startswith("!")
            text = text[1:] if negate else text
            found = any(c.lower().startswith(text.lower()) for c in candidates)
            if found != negate:
                hit = True
                break
        if not hit:
            return False
    return True


# ----------------------------------------------------------------------------- operations


def create_secret(p: Dict[str, Any], region: str) -> Dict[str, Any]:
    name = p.get("Name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise _invalid("Name must be 1-512 characters of letters, digits and /_+=.@-")
    value = _value(p)
    token = _token(p)
    existing = STORE.secrets.get(name)
    if existing is not None:
        if existing.get("DeletedDate"):
            raise _request("You can't create this secret because a secret with this name is already scheduled for "
                           "deletion.")
        version = existing["Versions"].get(token)
        if value is not None and version is not None and _same_value(version, value):
            return {"ARN": existing["ARN"], "Name": name, "VersionId": token}
        raise ApiError("ResourceExistsException", f"The operation failed because the secret {name} already exists.")
    suffix = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(6))
    now = _now()
    secret: Dict[str, Any] = {
        "Name": name,
        "ARN": f"arn:aws:secretsmanager:{region}:{ACCOUNT}:secret:{name}-{suffix}",
        "CreatedDate": now,
        "LastChangedDate": now,
        "Tags": _tags(p.get("Tags")),
        "Versions": {},
    }
    if p.get("Description") is not None:
        secret["Description"] = str(p["Description"])
    if p.get("KmsKeyId"):
        secret["KmsKeyId"] = str(p["KmsKeyId"])
    out = {"ARN": secret["ARN"], "Name": name}
    if value is not None:
        _add_version(secret, token, value, None)
        out["VersionId"] = token
    STORE.secrets[name] = secret
    STORE.save()
    return out


def get_secret_value(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.live(p.get("SecretId"))
    vid, version = _pick_version(secret, p)
    if secret.get("LastAccessedDate") != _today():
        secret["LastAccessedDate"] = _today()
        STORE.save()
    return _value_out(secret, vid, version)


def batch_get_secret_value(p: Dict[str, Any], region: str) -> Dict[str, Any]:
    ids, filters = p.get("SecretIdList"), p.get("Filters")
    if ids and filters:
        raise _invalid("Use either SecretIdList or Filters, not both.")
    values, errors = [], []
    if ids:
        if not isinstance(ids, list) or len(ids) > 20:
            raise _invalid("SecretIdList holds 1-20 secret ids.")
        for sid in ids:
            try:
                values.append(get_secret_value({"SecretId": sid}, region))
            except ApiError as exc:
                errors.append({"SecretId": sid, "ErrorCode": exc.code, "Message": exc.message})
        return {"SecretValues": values, "Errors": errors}
    matched = [s for n, s in sorted(STORE.secrets.items()) if not s.get("DeletedDate") and _matches(s, filters)]
    chunk, nxt = _page(p, matched, 20)
    for secret in chunk:
        try:
            values.append(get_secret_value({"SecretId": secret["Name"]}, region))
        except ApiError as exc:
            errors.append({"SecretId": secret["Name"], "ErrorCode": exc.code, "Message": exc.message})
    out: Dict[str, Any] = {"SecretValues": values, "Errors": errors}
    if nxt:
        out["NextToken"] = nxt
    return out


def put_secret_value(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.live(p.get("SecretId"))
    value = _value(p, required=True)
    token = _token(p)
    assert value is not None
    stages = _add_version(secret, token, value, p.get("VersionStages"))
    STORE.save()
    return {"ARN": secret["ARN"], "Name": secret["Name"], "VersionId": token, "VersionStages": stages}


def update_secret(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.live(p.get("SecretId"))
    value = _value(p)
    out = {"ARN": secret["ARN"], "Name": secret["Name"]}
    if p.get("Description") is not None:
        secret["Description"] = str(p["Description"])
    if p.get("KmsKeyId"):
        secret["KmsKeyId"] = str(p["KmsKeyId"])
    if value is not None:
        token = _token(p)
        _add_version(secret, token, value, None)
        out["VersionId"] = token
    secret["LastChangedDate"] = _now()
    STORE.save()
    return out


def describe_secret(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    return _describe(STORE.find(p.get("SecretId")))


def list_secrets(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    include_deleted = bool(p.get("IncludePlannedDeletion"))
    items = [s for _n, s in sorted(STORE.secrets.items())
             if (include_deleted or not s.get("DeletedDate")) and _matches(s, p.get("Filters"))]
    if p.get("SortOrder") == "desc":
        items.reverse()
    elif p.get("SortOrder") not in (None, "asc"):
        raise _invalid("SortOrder must be asc or desc.")
    chunk, nxt = _page(p, items)
    out: Dict[str, Any] = {"SecretList": [_describe(s, list_shape=True) for s in chunk]}
    if nxt:
        out["NextToken"] = nxt
    return out


def delete_secret(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    force = bool(p.get("ForceDeleteWithoutRecovery"))
    window = p.get("RecoveryWindowInDays")
    if force and window is not None:
        raise _invalid("You can't use ForceDeleteWithoutRecovery in conjunction with RecoveryWindowInDays.")
    out = {"ARN": secret["ARN"], "Name": secret["Name"]}
    if force:
        del STORE.secrets[secret["Name"]]
        out["DeletionDate"] = _now()
    else:
        if secret.get("DeletedDate"):
            raise _request("You can't perform this operation on the secret because it was already scheduled for "
                           "deletion.")
        try:
            days = int(window if window is not None else 30)
        except (TypeError, ValueError):
            raise _invalid("RecoveryWindowInDays must be a number.") from None
        if not 7 <= days <= 30:
            raise _invalid("RecoveryWindowInDays must be between 7 and 30 days.")
        secret["DeletedDate"] = _now()
        secret["DeletionDate"] = round(time.time() + days * 86400, 3)
        out["DeletionDate"] = secret["DeletionDate"]
    STORE.save()
    return out


def restore_secret(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    secret.pop("DeletedDate", None)
    secret.pop("DeletionDate", None)
    STORE.save()
    return {"ARN": secret["ARN"], "Name": secret["Name"]}


def list_secret_version_ids(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    versions = sorted(secret["Versions"].items(), key=lambda kv: kv[1]["CreatedDate"], reverse=True)
    if not p.get("IncludeDeprecated"):
        versions = [(vid, v) for vid, v in versions if v["Stages"]]
    rows = [{"VersionId": vid, "VersionStages": list(v["Stages"]), "CreatedDate": v["CreatedDate"]}
            for vid, v in versions]
    chunk, nxt = _page(p, rows)
    out: Dict[str, Any] = {"ARN": secret["ARN"], "Name": secret["Name"], "Versions": chunk}
    if nxt:
        out["NextToken"] = nxt
    return out


def update_secret_version_stage(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.live(p.get("SecretId"))
    stage = p.get("VersionStage")
    if not isinstance(stage, str) or not stage:
        raise _invalid("VersionStage is required.")
    remove_from, move_to = p.get("RemoveFromVersionId"), p.get("MoveToVersionId")
    versions = secret["Versions"]
    holder = next((vid for vid, v in versions.items() if stage in v["Stages"]), None)
    if holder and move_to and holder != move_to and remove_from != holder:
        raise _invalid(f"The staging label {stage} is currently attached to version {holder}, so you must "
                       "explicitly reference that version in RemoveFromVersionId.")
    for vid in (remove_from, move_to):
        if vid and vid not in versions:
            raise _invalid(f"Version {vid} does not exist.")
    if move_to:
        _move_stage(secret, stage, move_to)
    elif remove_from:
        if stage == "AWSCURRENT":
            raise _invalid("You can't remove AWSCURRENT; move it to another version instead.")
        if stage in versions[remove_from]["Stages"]:
            versions[remove_from]["Stages"].remove(stage)
    secret["LastChangedDate"] = _now()
    STORE.save()
    return {"ARN": secret["ARN"], "Name": secret["Name"]}


def tag_resource(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    secret["Tags"] = _merge_tags(secret.get("Tags") or [], _tags(p.get("Tags")))
    STORE.save()
    return {}


def untag_resource(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    keys = p.get("TagKeys") or []
    if not isinstance(keys, list):
        raise _invalid("TagKeys must be a list.")
    secret["Tags"] = [t for t in secret.get("Tags") or [] if t["Key"] not in keys]
    STORE.save()
    return {}


def get_random_password(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    try:
        length = int(p.get("PasswordLength") or 32)
    except (TypeError, ValueError):
        raise _invalid("PasswordLength must be a number.") from None
    if not 1 <= length <= 4096:
        raise _invalid("PasswordLength must be between 1 and 4096.")
    exclude = str(p.get("ExcludeCharacters") or "")
    groups = []
    if not p.get("ExcludeLowercase"):
        groups.append(string.ascii_lowercase)
    if not p.get("ExcludeUppercase"):
        groups.append(string.ascii_uppercase)
    if not p.get("ExcludeNumbers"):
        groups.append(string.digits)
    if not p.get("ExcludePunctuation"):
        groups.append(PUNCTUATION)
    if p.get("IncludeSpace"):
        groups.append(" ")
    groups = [g for g in ("".join(c for c in grp if c not in exclude) for grp in groups) if g]
    if not groups:
        raise _invalid("The password has no characters left to use.")
    every = p.get("RequireEachIncludedType", True)
    if every and length < len(groups):
        raise _invalid("PasswordLength is too short to include every character type.")
    pool = "".join(groups)
    rng = secrets.SystemRandom()
    chars = [rng.choice(g) for g in groups] if every else []
    chars += [rng.choice(pool) for _ in range(length - len(chars))]
    rng.shuffle(chars)
    return {"RandomPassword": "".join(chars)}


def put_resource_policy(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.live(p.get("SecretId"))
    policy = p.get("ResourcePolicy")
    try:
        json.loads(str(policy))
    except ValueError:
        raise ApiError("MalformedPolicyDocumentException", "ResourcePolicy must be a JSON policy document.") from None
    secret["ResourcePolicy"] = str(policy)
    STORE.save()
    return {"ARN": secret["ARN"], "Name": secret["Name"]}


def get_resource_policy(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    out = {"ARN": secret["ARN"], "Name": secret["Name"]}
    if secret.get("ResourcePolicy"):
        out["ResourcePolicy"] = secret["ResourcePolicy"]
    return out


def delete_resource_policy(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    secret = STORE.find(p.get("SecretId"))
    secret.pop("ResourcePolicy", None)
    STORE.save()
    return {"ARN": secret["ARN"], "Name": secret["Name"]}


def validate_resource_policy(p: Dict[str, Any], _region: str) -> Dict[str, Any]:
    try:
        json.loads(str(p.get("ResourcePolicy")))
    except ValueError:
        raise ApiError("MalformedPolicyDocumentException", "ResourcePolicy must be a JSON policy document.") from None
    return {"PolicyValidationPassed": True, "ValidationErrors": []}


def _unsupported(name: str) -> Any:
    def handler(_p: Dict[str, Any], _region: str) -> Dict[str, Any]:
        raise _request(f"{name} is not supported by this Secrets Manager compatible server (no rotation functions or "
                       "replica regions).")
    return handler


OPERATIONS = {
    "CreateSecret": create_secret,
    "GetSecretValue": get_secret_value,
    "BatchGetSecretValue": batch_get_secret_value,
    "PutSecretValue": put_secret_value,
    "UpdateSecret": update_secret,
    "DescribeSecret": describe_secret,
    "ListSecrets": list_secrets,
    "DeleteSecret": delete_secret,
    "RestoreSecret": restore_secret,
    "ListSecretVersionIds": list_secret_version_ids,
    "UpdateSecretVersionStage": update_secret_version_stage,
    "TagResource": tag_resource,
    "UntagResource": untag_resource,
    "GetRandomPassword": get_random_password,
    "PutResourcePolicy": put_resource_policy,
    "GetResourcePolicy": get_resource_policy,
    "DeleteResourcePolicy": delete_resource_policy,
    "ValidateResourcePolicy": validate_resource_policy,
    **{name: _unsupported(name) for name in ("RotateSecret", "CancelRotateSecret", "ReplicateSecretToRegions",
                                              "RemoveRegionsFromReplication", "StopReplicationToReplica")},
}
# Operations that change nothing: they are not logged.
READS = {"GetSecretValue", "BatchGetSecretValue", "DescribeSecret", "ListSecrets", "ListSecretVersionIds",
         "GetRandomPassword", "GetResourcePolicy", "ValidateResourcePolicy"}


def run(operation: str, params: Dict[str, Any], region: str, who: str) -> Dict[str, Any]:
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise ApiError("UnknownOperationException", f"Operation {operation} is not known.")
    if not isinstance(params, dict):
        raise ApiError("SerializationException", "The request body must be a JSON object.")
    with STORE.lock:
        STORE.purge()
        out = handler(params, region)
    if operation not in READS:
        log(operation, params.get("SecretId") or params.get("Name") or "", "by", who)
    return out


# ----------------------------------------------------------------------------- signature version 4


def _uri_encode(text: str) -> str:
    return quote(text, safe="-_.~")


def verify_sigv4(method: str, path: str, query: str, headers: Dict[str, str], body: bytes) -> Tuple[str, str]:
    """(access key id, region) of a correctly signed request, else :class:`ApiError` as AWS answers it."""
    auth = headers.get("authorization", "")
    if not auth:
        raise ApiError("MissingAuthenticationTokenException", "Missing Authentication Token")
    match = _AUTH.match(auth.strip())
    if not match:
        raise ApiError("IncompleteSignatureException", "Authorization header requires Credential, SignedHeaders and "
                       "Signature (AWS4-HMAC-SHA256).")
    credential, signed_headers, signature = match.groups()
    parts = credential.split("/")
    if len(parts) != 5 or parts[4] != "aws4_request":
        raise ApiError("IncompleteSignatureException", "The Credential is not valid.")
    access_key, day, region, service, _term = parts
    secret = CREDENTIALS.get(access_key)
    if secret is None:
        raise ApiError("UnrecognizedClientException", "The security token included in the request is invalid.")
    amz_date = headers.get("x-amz-date", "")
    try:
        when = datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ApiError("IncompleteSignatureException", "X-Amz-Date is missing or not valid.") from None
    if abs(time.time() - when.timestamp()) > SKEW:
        raise ApiError("InvalidSignatureException", f"Signature expired: {amz_date} is now earlier or later than "
                       "15 minutes from the server's time.")
    if day != amz_date[:8] or service != "secretsmanager":
        raise ApiError("InvalidSignatureException", "The credential scope does not match the request.")
    names = signed_headers.split(";")
    if "host" not in names or any(n not in headers for n in names):
        raise ApiError("InvalidSignatureException", "SignedHeaders must include host and only headers that were sent.")
    payload_hash = hashlib.sha256(body).hexdigest()
    claimed = headers.get("x-amz-content-sha256")
    if claimed and claimed != payload_hash:
        raise ApiError("InvalidSignatureException", "x-amz-content-sha256 does not match the request body.")
    pairs = sorted((_uri_encode(k), _uri_encode(v)) for k, v in parse_qsl(query, keep_blank_values=True))
    canonical = "\n".join([
        method.upper(),
        path or "/",
        "&".join(f"{k}={v}" for k, v in pairs),
        "".join(f"{n}:{' '.join(headers[n].split())}\n" for n in names),
        signed_headers,
        payload_hash,
    ])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    key = ("AWS4" + secret).encode("utf-8")
    for part in (day, region, service, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ApiError("InvalidSignatureException", "The request signature we calculated does not match the "
                       "signature you provided. Check your AWS Secret Access Key and signing method.")
    return access_key, region


# ----------------------------------------------------------------------------- web console sessions

SESSIONS: Dict[str, Dict[str, Any]] = {}
FAILURES: Dict[str, List[float]] = {}
_session_lock = threading.Lock()


def _check_login(user: str, password: str) -> bool:
    if UI_PASSWORD:
        return hmac.compare_digest(password.encode("utf-8"), UI_PASSWORD.encode("utf-8"))
    secret = CREDENTIALS.get(user)
    # compare_digest on a dummy keeps the time the same for an unknown user.
    return hmac.compare_digest(password.encode("utf-8"), (secret or "\0" * 32).encode("utf-8")) and secret is not None


def _throttled(ip: str) -> bool:
    now = time.time()
    with _session_lock:
        recent = [t for t in FAILURES.get(ip, []) if now - t < 300]
        FAILURES[ip] = recent
        return len(recent) >= 10


def _failed(ip: str) -> None:
    with _session_lock:
        FAILURES.setdefault(ip, []).append(time.time())


def _new_session(user: str) -> Tuple[str, str]:
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with _session_lock:
        now = time.time()
        for key in [k for k, s in SESSIONS.items() if s["expires"] < now]:
            del SESSIONS[key]
        SESSIONS[sid] = {"user": user, "csrf": csrf, "expires": now + SESSION_TTL}
    return sid, csrf


def _session(cookie_header: str) -> Optional[Dict[str, Any]]:
    for part in cookie_header.split(";"):
        name, _sep, value = part.strip().partition("=")
        if name == "sm_session":
            with _session_lock:
                found = SESSIONS.get(value)
                if found and found["expires"] > time.time():
                    return {**found, "id": value}
    return None


def _ui_html() -> bytes:
    here = Path(__file__).resolve().parent
    for candidate in ([Path(UI_FILE)] if UI_FILE else []) + [here / "index.html", here.parent / "ui" / "index.html"]:
        if candidate.is_file():
            return candidate.read_bytes()
    return b"<!doctype html><title>Secrets console</title><p>The console page (index.html) is missing."


# ----------------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = f"pskit-secretsmanager/{VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-amzn-RequestId", str(uuid.uuid4()))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: Any, extra: Optional[Dict[str, str]] = None) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/x-amz-json-1.1", extra)

    def _error(self, exc: ApiError, ui: bool = False) -> None:
        body = {"__type": exc.code, "message": exc.message}
        self._json(exc.status, body, None if ui else {"x-amzn-ErrorType": exc.code})

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            raise ApiError("SerializationException", "The request body is too large.", 413)
        return self.rfile.read(length) if length else b""

    def _params(self, body: bytes) -> Dict[str, Any]:
        try:
            data = json.loads(body.decode("utf-8")) if body.strip() else {}
        except (ValueError, UnicodeDecodeError):
            raise ApiError("SerializationException", "The request body is not valid JSON.") from None
        if not isinstance(data, dict):
            raise ApiError("SerializationException", "The request body must be a JSON object.")
        return data

    def _headers(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for name in self.headers.keys():
            low = name.lower()
            if low not in out:
                out[low] = ",".join(v.strip() for v in self.headers.get_all(name) or [])
        return out

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        path = urlsplit(self.path).path
        if path in ("/health", "/healthz"):
            self._send(200, json.dumps({"status": "ok", "secrets": len(STORE.secrets), "version": VERSION}).encode(),
                       "application/json")
        elif UI_ON and path in ("/", "/ui"):
            self._send(302, b"", "text/plain", {"Location": "/ui/"})
        elif UI_ON and path == "/ui/":
            self._send(200, _ui_html(), "text/html; charset=utf-8", {
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                           "connect-src 'self'; img-src data:; frame-ancestors 'none'; form-action 'self'",
            })
        elif UI_ON and path == "/ui/session":
            found = _session(self.headers.get("Cookie") or "")
            if AUTH == "off":
                self._send(200, json.dumps({"authenticated": True, "user": "", "auth": False, "csrf": ""}).encode(),
                           "application/json", {"Cache-Control": "no-store"})
            else:
                data = {"authenticated": bool(found), "user": found["user"] if found else "", "auth": True,
                        "csrf": found["csrf"] if found else "", "password_login": bool(UI_PASSWORD)}
                self._send(200, json.dumps(data).encode(), "application/json", {"Cache-Control": "no-store"})
        else:
            self._error(ApiError("NotFound", "Not found. The Secrets Manager API takes POST / with X-Amz-Target.", 404))

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        try:
            body = self._body()
            if url.path.startswith("/ui/"):
                if not UI_ON:
                    raise ApiError("NotFound", "The web console is off (SM_UI=off).", 404)
                self._ui_post(url.path, body)
                return
            headers = self._headers()
            target = headers.get("x-amz-target", "")
            service, _dot, operation = target.partition(".")
            if service != "secretsmanager" or not operation:
                raise ApiError("UnknownOperationException", "X-Amz-Target must be secretsmanager.<Operation>.")
            if AUTH == "off":
                who, region = "anonymous", REGION
            else:
                who, region = verify_sigv4("POST", url.path, url.query, headers, body)
            self._json(200, run(operation, self._params(body), region, who))
        except ApiError as exc:
            self._error(exc)
        except Exception as exc:  # noqa: BLE001 - answer every request
            log("error:", repr(exc))
            self._error(ApiError("InternalServiceError", "The server failed to process the request.", 500))

    def _ui_post(self, path: str, body: bytes) -> None:
        ip = self.client_address[0]
        if path == "/ui/login":
            if AUTH == "off":
                self._json(200, {"authenticated": True, "csrf": ""}, None)
                return
            if _throttled(ip):
                raise ApiError("ThrottlingException", "Too many failed sign-ins; wait 5 minutes.", 429)
            data = self._params(body)
            user, password = str(data.get("user") or ""), str(data.get("password") or "")
            if not _check_login(user, password):
                _failed(ip)
                log("console sign-in refused from", ip)
                raise ApiError("AccessDeniedException", "Wrong access key or password.", 401)
            sid, csrf = _new_session(user or "console")
            log("console sign-in", user or "console", "from", ip)
            self._json(200, {"authenticated": True, "user": user, "csrf": csrf}, {
                "Set-Cookie": f"sm_session={sid}; Path=/ui/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}",
                "Cache-Control": "no-store",
            })
            return
        found = _session(self.headers.get("Cookie") or "")
        if path == "/ui/logout":
            if found:
                with _session_lock:
                    SESSIONS.pop(found["id"], None)
            self._json(200, {"authenticated": False}, {"Set-Cookie": "sm_session=; Path=/ui/; HttpOnly; Max-Age=0"})
            return
        if not path.startswith("/ui/api/"):
            raise ApiError("NotFound", "Not found.", 404)
        if AUTH != "off":
            if not found:
                raise ApiError("AccessDeniedException", "Sign in first.", 401)
            if not hmac.compare_digest(self.headers.get("X-SM-CSRF") or "", found["csrf"]):
                raise ApiError("AccessDeniedException", "The console's request token is missing; reload the page.", 403)
        who = f"console:{found['user']}" if found else "console"
        self._json(200, run(path[len("/ui/api/"):], self._params(body), REGION, who), {"Cache-Control": "no-store"})


def check() -> int:
    import urllib.request

    host = "127.0.0.1" if BIND in ("0.0.0.0", "") else BIND  # noqa: S104
    try:
        with urllib.request.urlopen(f"http://{host}:{PORT}/health", timeout=3) as resp:  # noqa: S310 - local health
            return 0 if resp.status == 200 else 1
    except OSError:
        return 1


def main() -> int:
    global STORE
    if "--check" in sys.argv[1:]:
        return check()
    if AUTH not in ("sigv4", "off"):
        print("SM_AUTH must be sigv4 or off", file=sys.stderr)
        return 2
    if AUTH != "off" and not CREDENTIALS:
        print("set SM_SECRET_ACCESS_KEY (and SM_ACCESS_KEY_ID), or SM_CREDENTIALS=id:secret, or SM_AUTH=off",
              file=sys.stderr)
        return 2
    STORE = Store(STORE_FILE)
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    server.daemon_threads = True
    log(f"Secrets Manager compatible server {VERSION} (Python) on {BIND}:{PORT}, data {STORE_FILE}, "
        f"{len(STORE.secrets)} secrets, auth {AUTH}" + (", console /ui/" if UI_ON else ""))
    if AUTH == "off":
        log("warning: SM_AUTH=off, anyone who reaches the port can read every secret")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
