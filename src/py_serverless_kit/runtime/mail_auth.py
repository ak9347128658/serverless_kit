"""Who really sent a mail: SPF, DKIM and DMARC checks for the host's SMTP server (standard library only).

Anyone can write ``From: ceo@kavion.ai``. Receivers check that:

* **SPF** (RFC 7208): the connecting IP is allowed by the sender domain's ``v=spf1`` TXT record (envelope MAIL FROM,
  or the EHLO name for bounces).
* **DKIM** (RFC 6376): the ``DKIM-Signature`` header verifies with the public key at
  ``<selector>._domainkey.<domain>`` (rsa-sha256 / rsa-sha1, simple / relaxed canonicalization).
* **DMARC** (RFC 7489): SPF or DKIM passed *for the domain in From:* (aligned, relaxed or strict), and what the
  domain's ``_dmarc`` record asks receivers to do (``p=none / quarantine / reject``).

:func:`authenticate` returns ``{"spf": {...}, "dkim": [...], "dmarc": {...}, "summary": "spf=pass dkim=pass ..."}``,
the same results receivers put in an ``Authentication-Results`` header. DNS is asked with a small UDP/TCP client
(``/etc/resolv.conf``, ``PSKIT_DNS_SERVERS`` or 1.1.1.1 / 8.8.8.8). ed25519 DKIM keys and SPF macros are not
checked (``neutral``).
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import random
import re
import socket
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple

from .mail import split_host

QueryFn = Callable[[str, str], List[str]]
_TYPES = {"A": 1, "MX": 15, "TXT": 16, "AAAA": 28, "CNAME": 5}


class DnsError(OSError):
    """The DNS server did not answer (a temporary error, not "no such record")."""


# ----------------------------------------------------------------------------- DNS


def _servers() -> List[str]:
    configured = [s.strip() for s in os.environ.get("PSKIT_DNS_SERVERS", "").split(",") if s.strip()]
    if configured:
        return configured
    found: List[str] = []
    try:
        with open("/etc/resolv.conf", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    found.append(parts[1])
    except OSError:
        pass
    return found or ["1.1.1.1", "8.8.8.8"]


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        raw = label.encode("idna") if label else b""
        if not 0 < len(raw) < 64:
            raise ValueError(f"invalid DNS name {name!r}")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def _read_name(msg: bytes, offset: int) -> Tuple[str, int]:
    labels: List[str] = []
    end = -1
    jumps = 0
    while True:
        length = msg[offset]
        if length & 0xC0 == 0xC0:
            if end < 0:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | msg[offset + 1]
            jumps += 1
            if jumps > 20:
                raise DnsError("DNS name compression loop")
            continue
        if length == 0:
            offset += 1
            break
        labels.append(msg[offset + 1:offset + 1 + length].decode("ascii", "replace"))
        offset += 1 + length
    return ".".join(labels), (end if end >= 0 else offset)


def _parse(msg: bytes, qid: int, qtype: int) -> Tuple[List[str], bool]:
    rid, flags, qd, an = struct.unpack(">HHHH", msg[:8])
    if rid != qid:
        raise DnsError("DNS answer id mismatch")
    truncated = bool(flags & 0x0200)
    rcode = flags & 0x000F
    if rcode == 3:  # NXDOMAIN
        return [], truncated
    if rcode != 0:
        raise DnsError(f"DNS server error (rcode {rcode})")
    offset = 12
    for _ in range(qd):
        _name, offset = _read_name(msg, offset)
        offset += 4
    out: List[str] = []
    for _ in range(an):
        _name, offset = _read_name(msg, offset)
        rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", msg[offset:offset + 10])
        offset += 10
        rdata = msg[offset:offset + rdlen]
        if rtype == qtype:
            if rtype == 1:
                out.append(socket.inet_ntoa(rdata))
            elif rtype == 28:
                out.append(str(ipaddress.IPv6Address(rdata)))
            elif rtype == 15:
                pref = struct.unpack(">H", rdata[:2])[0]
                host, _ = _read_name(msg, offset + 2)
                out.append(f"{pref} {host}")
            elif rtype == 16:
                parts, i = [], 0
                while i < len(rdata):
                    size = rdata[i]
                    parts.append(rdata[i + 1:i + 1 + size].decode("utf-8", "replace"))
                    i += 1 + size
                out.append("".join(parts))
            elif rtype == 5:
                out.append(_read_name(msg, offset)[0])
        offset += rdlen
    return out, truncated


def query(name: str, rtype: str, timeout: float = 3.0) -> List[str]:
    """Records of ``name``: A / AAAA as addresses, MX as ``"10 mx.example.com"``, TXT joined. [] when none."""
    qtype = _TYPES[rtype]
    last: Optional[Exception] = None
    for server in _servers():
        qid = random.randint(0, 0xFFFF)  # noqa: S311 - a DNS id, not a secret
        packet = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + _encode_name(name) + struct.pack(">HH", qtype, 1)
        family = socket.AF_INET6 if ":" in server else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(packet, (server, 53))
                data, _addr = sock.recvfrom(65535)
            records, truncated = _parse(data, qid, qtype)
            if not truncated:
                return records
            with socket.create_connection((server, 53), timeout=timeout) as conn:  # too big for UDP: TCP
                conn.sendall(struct.pack(">H", len(packet)) + packet)
                size = struct.unpack(">H", _recv(conn, 2))[0]
                return _parse(_recv(conn, size), qid, qtype)[0]
        except (OSError, struct.error, IndexError) as exc:
            last = exc
    raise DnsError(f"no DNS answer for {name} {rtype}: {last}")


def _recv(conn: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise DnsError("DNS TCP connection closed")
        data += chunk
    return data


def org_domain(host: str) -> str:
    return split_host(host)["domain"]


# ----------------------------------------------------------------------------- SPF


class _Spf:
    def __init__(self, ip: str, lookup: QueryFn) -> None:
        self.ip = ipaddress.ip_address(ip)
        self.lookup = lookup
        self.count = 0

    def _dns(self, name: str, rtype: str) -> List[str]:
        return self.lookup(name, rtype)

    def _limit(self) -> None:
        self.count += 1
        if self.count > 10:
            raise ValueError("more than 10 DNS lookups")

    def _addresses(self, host: str) -> List[str]:
        return self._dns(host, "AAAA" if self.ip.version == 6 else "A")

    def _in(self, addresses: List[str], cidr4: int, cidr6: int) -> bool:
        bits = cidr6 if self.ip.version == 6 else cidr4
        for addr in addresses:
            try:
                if self.ip in ipaddress.ip_network(f"{addr}/{bits}", strict=False):
                    return True
            except ValueError:
                continue
        return False

    def check(self, domain: str, depth: int = 0) -> str:
        if depth > 10:
            raise ValueError("SPF include loop")
        records = [t for t in self._dns(domain, "TXT") if t.lower().startswith("v=spf1") and
                   (len(t) == 6 or t[6] == " ")]
        if not records:
            return "none"
        if len(records) > 1:
            raise ValueError(f"{domain} has {len(records)} SPF records")
        redirect = ""
        for term in records[0].split()[1:]:
            low = term.lower()
            if low.startswith("redirect="):
                redirect = term.split("=", 1)[1]
                continue
            if "=" in low.split(":", 1)[0]:  # exp= and unknown modifiers
                continue
            qualifier = term[0] if term[0] in "+-~?" else "+"
            mech = term[1:] if term[0] in "+-~?" else term
            if self._match(mech, domain, depth):
                return {"+": "pass", "-": "fail", "~": "softfail", "?": "neutral"}[qualifier]
        if redirect:
            self._limit()
            return self.check(redirect, depth + 1)
        return "neutral"

    def _match(self, mech: str, domain: str, depth: int) -> bool:
        name, _, arg = mech.partition(":")
        name = name.lower()
        if name in ("ip4", "ip6"):  # the CIDR belongs to the network itself
            try:
                return self.ip in ipaddress.ip_network(arg, strict=False)
            except ValueError:
                return False
        cidr4, cidr6 = 32, 128
        if "/" in (arg if arg else name):
            target = arg if arg else name
            pieces = target.split("/")
            if arg:
                arg = pieces[0]
            else:
                name = pieces[0]
            if len(pieces) > 1 and pieces[1]:
                cidr4 = int(pieces[1])
            if len(pieces) > 3 and pieces[3]:
                cidr6 = int(pieces[3])
        if "%" in arg:  # macros are not expanded
            return False
        if name == "all":
            return True
        if name == "a":
            self._limit()
            return self._in(self._addresses(arg or domain), cidr4, cidr6)
        if name == "mx":
            self._limit()
            hosts = [r.split()[-1] for r in self._dns(arg or domain, "MX")][:10]
            return any(self._in(self._addresses(h), cidr4, cidr6) for h in hosts)
        if name == "include":
            self._limit()
            result = self.check(arg, depth + 1)
            if result == "none":
                raise ValueError(f"include:{arg} has no SPF record")
            return result == "pass"
        if name == "exists":
            self._limit()
            return bool(self._dns(arg, "A"))
        return False  # ptr (deprecated) and unknown mechanisms


def check_spf(ip: str, mail_from: str, helo: str, lookup: QueryFn = query) -> Dict[str, Any]:
    domain = (mail_from.rpartition("@")[2] if "@" in mail_from else "") or helo
    domain = domain.strip().strip(".").lower()
    if not domain or "." not in domain:
        return {"result": "none", "domain": domain, "reason": "no sender domain"}
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return {"result": "none", "domain": domain, "reason": "not received over SMTP"}
    try:
        result = _Spf(ip, lookup).check(domain)
    except DnsError as exc:
        return {"result": "temperror", "domain": domain, "reason": str(exc)}
    except ValueError as exc:
        return {"result": "permerror", "domain": domain, "reason": str(exc)}
    return {"result": result, "domain": domain, "reason": f"{ip} is {result} for {domain}'s SPF record"
            if result != "none" else f"{domain} has no SPF record"}


# ----------------------------------------------------------------------------- DKIM

_SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_SHA1_INFO = bytes.fromhex("3021300906052b0e03021a05000414")


def _split(raw: bytes) -> Tuple[List[bytes], bytes]:
    raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        head, body = raw, b""
    headers: List[bytes] = []
    for line in head.split(b"\r\n"):
        if line[:1] in (b" ", b"\t") and headers:
            headers[-1] += b"\r\n" + line
        elif line:
            headers.append(line)
    return headers, body


def _tags(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in text.split(";"):
        key, sep, value = part.partition("=")
        if sep:
            out[key.strip().lower()] = re.sub(r"\s+", "", value) if key.strip().lower() in ("b", "bh", "p") \
                else value.strip()
    return out


def canonical_body(body: bytes, relaxed: bool) -> bytes:
    if relaxed:
        lines = [re.sub(rb"[ \t]+", b" ", line).rstrip(b" \t") for line in body.split(b"\r\n")]
        body = b"\r\n".join(lines)
    while body.endswith(b"\r\n\r\n"):
        body = body[:-2]
    if relaxed:
        body = body.rstrip(b"\r\n")
        return body + b"\r\n" if body else b""
    body = body.rstrip(b"\r\n")
    return body + b"\r\n"


def canonical_header(header: bytes, relaxed: bool) -> bytes:
    if not relaxed:
        return header
    name, _, value = header.partition(b":")
    value = re.sub(rb"[ \t]+", b" ", value.replace(b"\r\n", b"")).strip(b" \t")
    return name.strip().lower() + b":" + value


def _der(data: bytes, offset: int) -> Tuple[int, bytes, int]:
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        size = length & 0x7F
        length = int.from_bytes(data[offset:offset + size], "big")
        offset += size
    return tag, data[offset:offset + length], offset + length


def rsa_key(der: bytes) -> Tuple[int, int]:
    """(n, e) from a SubjectPublicKeyInfo or a PKCS#1 RSAPublicKey."""
    tag, seq, _ = _der(der, 0)
    if tag != 0x30:
        raise ValueError("not a DER sequence")
    first_tag, first, after = _der(seq, 0)
    if first_tag == 0x02:  # PKCS#1: SEQUENCE { n, e }
        _t, e_raw, _ = _der(seq, after)
        return int.from_bytes(first, "big"), int.from_bytes(e_raw, "big")
    _t, bits, _ = _der(seq, after)
    return rsa_key(bits[1:])  # BIT STRING: unused-bits byte, then RSAPublicKey


def rsa_verify(n: int, e: int, signature: bytes, digest: bytes, info: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    t = info + digest
    return em == b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def verify_dkim(raw: bytes, lookup: QueryFn = query, limit: int = 5) -> List[Dict[str, Any]]:
    headers, body = _split(raw)
    results: List[Dict[str, Any]] = []
    for header in [h for h in headers if h.split(b":", 1)[0].strip().lower() == b"dkim-signature"][:limit]:
        tags = _tags(header.split(b":", 1)[1].decode("utf-8", "replace"))
        domain, selector = tags.get("d", "").lower(), tags.get("s", "")
        item: Dict[str, Any] = {"domain": domain, "selector": selector, "result": "permerror", "reason": ""}
        results.append(item)
        algo = tags.get("a", "").lower()
        if not all(tags.get(t) for t in ("v", "a", "b", "bh", "d", "h", "s")) or tags.get("v") != "1":
            item["reason"] = "incomplete DKIM-Signature"
            continue
        if algo not in ("rsa-sha256", "rsa-sha1"):
            item.update(result="neutral", reason=f"{algo} is not checked")
            continue
        hasher = hashlib.sha256 if algo == "rsa-sha256" else hashlib.sha1
        hcanon, _, bcanon = tags.get("c", "simple/simple").lower().partition("/")
        canon = canonical_body(body, (bcanon or "simple") == "relaxed")
        if tags.get("l", "").isdigit():
            canon = canon[: int(tags["l"])]
        if base64.b64encode(hasher(canon).digest()).decode() != tags["bh"]:
            item.update(result="fail", reason="body changed after signing (body hash mismatch)")
            continue
        relaxed = hcanon == "relaxed"
        pool = list(headers)
        signed = b""
        for name in [n.strip().lower() for n in tags["h"].split(":") if n.strip()]:
            for i in range(len(pool) - 1, -1, -1):
                if pool[i].split(b":", 1)[0].strip().lower().decode("ascii", "replace") == name:
                    signed += canonical_header(pool.pop(i), relaxed) + b"\r\n"
                    break
        name_part, _, value = header.partition(b":")
        value = re.sub(rb"(^|;)(\s*b\s*=)[^;]*", rb"\1\2", value)
        signed += canonical_header(name_part + b":" + value, relaxed)
        try:
            records = [r for r in lookup(f"{selector}._domainkey.{domain}", "TXT") if "p=" in r]
        except DnsError as exc:
            item.update(result="temperror", reason=str(exc))
            continue
        if not records:
            item["reason"] = f"no key at {selector}._domainkey.{domain}"
            continue
        key = _tags(records[0])
        if not key.get("p"):
            item["reason"] = "the key was revoked (empty p=)"
            continue
        if key.get("k", "rsa").lower() != "rsa":
            item.update(result="neutral", reason=f"{key.get('k')} keys are not checked")
            continue
        try:
            n, e = rsa_key(base64.b64decode(key["p"]))
            ok = rsa_verify(n, e, base64.b64decode(tags["b"]), hasher(signed).digest(),
                            _SHA256_INFO if algo == "rsa-sha256" else _SHA1_INFO)
        except (ValueError, IndexError):
            item["reason"] = "the published key cannot be read"
            continue
        item.update(result="pass" if ok else "fail", reason="signature verified" if ok else "signature does not match")
    return results


# ----------------------------------------------------------------------------- DMARC


def check_dmarc(from_host: str, spf: Dict[str, Any], dkim: List[Dict[str, Any]],
                lookup: QueryFn = query) -> Dict[str, Any]:
    from_host = from_host.lower().strip(".")
    if not from_host:
        return {"result": "none", "policy": "none", "domain": "", "reason": "no From: domain"}
    org = org_domain(from_host)
    record, where = "", from_host
    try:
        for name in dict.fromkeys([from_host, org]):
            found = [r for r in lookup(f"_dmarc.{name}", "TXT") if r.lower().startswith("v=dmarc1")]
            if found:
                record, where = found[0], name
                break
    except DnsError as exc:
        return {"result": "temperror", "policy": "none", "domain": from_host, "reason": str(exc)}
    tags = _tags(record) if record else {}
    strict_spf, strict_dkim = tags.get("aspf", "r") == "s", tags.get("adkim", "r") == "s"

    def aligned(domain: str, strict: bool) -> bool:
        domain = domain.lower().strip(".")
        return domain == from_host if strict else bool(domain) and org_domain(domain) == org

    spf_ok = spf.get("result") == "pass" and aligned(str(spf.get("domain") or ""), strict_spf)
    dkim_ok = any(d.get("result") == "pass" and aligned(str(d.get("domain") or ""), strict_dkim) for d in dkim)
    policy = tags.get("sp") if where != from_host and tags.get("sp") else tags.get("p", "none")
    if not record:
        result = "none"
    else:
        result = "pass" if (spf_ok or dkim_ok) else "fail"
    return {"result": result, "policy": (policy or "none").lower() if record else "none", "domain": from_host,
            "aligned": {"spf": spf_ok, "dkim": dkim_ok},
            "reason": "no DMARC record" if not record else
            ("SPF or DKIM passed for the From: domain" if result == "pass" else
             "neither SPF nor DKIM passed for the From: domain")}


def authenticate(raw: bytes, envelope: Dict[str, Any], from_host: str, lookup: QueryFn = query) -> Dict[str, Any]:
    """SPF, DKIM and DMARC of one received message."""
    spf = check_spf(str(envelope.get("remote_ip") or ""), str(envelope.get("mail_from") or ""),
                    str(envelope.get("helo") or ""), lookup)
    try:
        dkim = verify_dkim(raw, lookup)
    except Exception as exc:  # noqa: BLE001 - a malformed message must not stop delivery
        dkim = [{"domain": "", "selector": "", "result": "permerror", "reason": str(exc)}]
    dmarc = check_dmarc(from_host, spf, dkim, lookup)
    dkim_text = " ".join(f"dkim={d['result']}({d['domain']})" for d in dkim) or "dkim=none"
    return {"spf": spf, "dkim": dkim, "dmarc": dmarc,
            "summary": f"spf={spf['result']}({spf['domain']}) {dkim_text} dmarc={dmarc['result']}"
                       f"(p={dmarc['policy']})"}


def spoofed(auth: Dict[str, Any], from_host: str, own_domains: List[str]) -> Optional[str]:
    """Why the mail should be blocked (``block_spoofed``), or None.

    Blocked: DMARC fails and the domain asks for quarantine / reject, or the From: address is one of *this server's*
    domains (``ceo@kavion.ai`` to ``support@kavion.ai``) and neither SPF nor DKIM passed for it.
    """
    dmarc = auth.get("dmarc") or {}
    if dmarc.get("result") == "fail" and dmarc.get("policy") in ("quarantine", "reject"):
        return f"DMARC failed for {from_host} (policy {dmarc.get('policy')})"
    host = from_host.lower()
    ours = any(host == d or host.endswith("." + d) for d in (x.lower() for x in own_domains))
    if ours and not any((dmarc.get("aligned") or {}).values()):
        return f"From: {host} is one of this server's domains, but neither SPF nor DKIM passed for it"
    return None
