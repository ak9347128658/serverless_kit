"""SPF, DKIM and DMARC checks of received mail (runtime/mail_auth.py), with fake DNS."""

from __future__ import annotations

import pytest

from py_serverless_kit.runtime import mail_auth as ma

# Signed by dkimpy (an independent DKIM implementation) with a throwaway 1024-bit key.
DKIM_KEY = 'MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCuVF4NO5MxX+RgNNFe7XKRx01oPZGibKAEZsT6c2NBzmo5sAS49Ncy/R2+typUif7dnBMvBWUKs0Up5Gv/pmvA4z517LhmLYek3/P8zRWlLhJZizw4PsYXH5wtoXGWMoGmOMOnj7rwCxSsKMC399WtSQqEezQuxiWTMkXQL8Hk0QIDAQAB'
SIGNED = {
    "relaxed": 'DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed; d=client.org;\r\n i=@client.org; q=dns/txt; s=s1; t=1792756770; h=from : to : subject :\r\n date : message-id; bh=gLFQxywFzudWXhAPekjwTpevXM98rkAscpn4UvFsmrE=;\r\n b=E1fFlg8ZweUr/jVWOlWoc/8heeJ/sZ3ykEe8ELdRT1A/s+sq70lot4brzY9i+4lYgcV55\r\n jlRCo8Ud06gYnY6vIIoE8INgvS8W+5wtX4Cynn+1E7HJG0OQS26rvclHSFsDerC3Jd/oxmg\r\n 7C6g0uhOskz0mVosv/KJOlUYdWBhAfY=\r\nFrom: Ann <ann@client.org>\r\nTo: help@support.kavion.ai\r\nSubject:  Printer   broken \r\nDate: Fri, 23 Oct 2026 10:00:00 +0000\r\nMessage-ID: <x@client.org>\r\n\r\nHello   there \r\nline two\r\n\r\n',
    "simple": 'DKIM-Signature: v=1; a=rsa-sha256; c=simple/simple; d=client.org;\r\n i=@client.org; q=dns/txt; s=s1; t=1792756770; h=from : to : subject :\r\n date : message-id; bh=dpK0UniF210oevFXwxbBGFfQ9P5IcsdkmfANiipQFkY=;\r\n b=k4MZ8vK5StklQ55JsgATvCY8v+j+R1/2IUqDQiYupjdikeTNvikh+F76J5ud2sGqpwiVw\r\n ejp9X3iy/58r23bm7ZMNQrtR5ZKNB3k2FAcqniZJLp0UV+zV0GUCc3A5Q3w5ZcLHJQdKSPJ\r\n h1nscvWBbkgXRDL9YusHC+5eFoBh6qw=\r\nFrom: Ann <ann@client.org>\r\nTo: help@support.kavion.ai\r\nSubject:  Printer   broken \r\nDate: Fri, 23 Oct 2026 10:00:00 +0000\r\nMessage-ID: <x@client.org>\r\n\r\nHello   there \r\nline two\r\n\r\n',
}


def dns(records):
    def lookup(name, rtype):
        value = records.get((name.lower().rstrip("."), rtype))
        if isinstance(value, Exception):
            raise value
        return list(value or [])
    return lookup


KEY_DNS = {("s1._domainkey.client.org", "TXT"): [f"v=DKIM1; k=rsa; p={DKIM_KEY}"]}


@pytest.mark.parametrize("canon", ["relaxed", "simple"])
def test_dkim_verifies_a_signature_made_by_another_implementation(canon):
    raw = SIGNED[canon].encode()
    assert [r["result"] for r in ma.verify_dkim(raw, dns(KEY_DNS))] == ["pass"]
    received = b"Received: from mx.client.org ([198.51.100.7]) by mx.kavion.ai; now\r\n" + raw
    assert ma.verify_dkim(received, dns(KEY_DNS))[0]["result"] == "pass"  # our own header on top
    body = ma.verify_dkim(raw.replace(b"line two", b"line 2"), dns(KEY_DNS))[0]
    assert body["result"] == "fail" and "body" in body["reason"]
    header = ma.verify_dkim(raw.replace(b"Printer", b"Scanner"), dns(KEY_DNS))[0]
    assert header["result"] == "fail" and header["reason"] == "signature does not match"
    assert ma.verify_dkim(raw, dns({}))[0]["result"] == "permerror"  # no key published
    revoked = ma.verify_dkim(raw, dns({("s1._domainkey.client.org", "TXT"): ["v=DKIM1; p="]}))[0]
    assert revoked["reason"].startswith("the key was revoked")
    down = ma.verify_dkim(raw, dns({("s1._domainkey.client.org", "TXT"): ma.DnsError("timeout")}))[0]
    assert down["result"] == "temperror"
    assert ma.verify_dkim(b"From: a@b.c\r\n\r\nhi", dns({})) == []


SPF = {
    ("client.org", "TXT"): ["v=spf1 ip4:198.51.100.0/24 include:_spf.mailer.net a:relay.client.org mx -all"],
    ("_spf.mailer.net", "TXT"): ["v=spf1 ip6:2001:db8::/32 ip4:192.0.2.10 ~all"],
    ("relay.client.org", "A"): ["203.0.113.50"],
    ("client.org", "MX"): ["10 mx.client.org"],
    ("mx.client.org", "A"): ["203.0.113.60"],
    ("soft.org", "TXT"): ["v=spf1 redirect=client.org"],
    ("loop.org", "TXT"): ["v=spf1 include:loop.org -all"],
    ("two.org", "TXT"): ["v=spf1 -all", "v=spf1 +all"],
    ("broken.org", "TXT"): ["v=spf1 include:nothing.org -all"],
}


@pytest.mark.parametrize("ip,result", [
    ("198.51.100.7", "pass"),     # ip4 with a CIDR
    ("192.0.2.10", "pass"),       # include
    ("2001:db8::25", "pass"),     # ip6 in an include
    ("203.0.113.50", "pass"),     # a:host
    ("203.0.113.60", "pass"),     # mx
    ("203.0.113.99", "fail"),     # -all
])
def test_spf_mechanisms(ip, result):
    assert ma.check_spf(ip, "ann@client.org", "x", dns(SPF))["result"] == result


def test_spf_redirect_errors_and_fallbacks():
    lookup = dns(SPF)
    assert ma.check_spf("198.51.100.7", "ann@soft.org", "x", lookup)["result"] == "pass"
    assert ma.check_spf("198.51.100.7", "ann@loop.org", "x", lookup)["result"] == "permerror"
    assert ma.check_spf("198.51.100.7", "ann@two.org", "x", lookup)["result"] == "permerror"
    assert ma.check_spf("198.51.100.7", "ann@broken.org", "x", lookup)["result"] == "permerror"
    assert ma.check_spf("198.51.100.7", "ann@none.org", "x", lookup)["result"] == "none"
    assert ma.check_spf("198.51.100.7", "", "client.org", lookup)["result"] == "pass"  # bounce: the EHLO name
    assert ma.check_spf("admin-api", "ann@client.org", "x", lookup)["result"] == "none"
    down = dns({("client.org", "TXT"): ma.DnsError("timeout")})
    assert ma.check_spf("198.51.100.7", "ann@client.org", "x", down)["result"] == "temperror"


def test_dmarc_alignment_and_policy():
    lookup = dns({("_dmarc.client.org", "TXT"): ["v=DMARC1; p=reject; sp=quarantine; adkim=s"]})
    spf_pass = {"result": "pass", "domain": "bounces.client.org"}
    assert ma.check_dmarc("client.org", spf_pass, [], lookup)["result"] == "pass"  # relaxed SPF alignment
    dkim_sub = [{"result": "pass", "domain": "news.client.org"}]
    failed = ma.check_dmarc("client.org", {"result": "fail", "domain": "client.org"}, dkim_sub, lookup)
    assert failed["result"] == "fail" and failed["policy"] == "reject"  # adkim=s: a subdomain does not align
    sub = ma.check_dmarc("eu.client.org", {"result": "none", "domain": ""}, [], lookup)
    assert sub["policy"] == "quarantine"  # sp= for subdomains (organizational record)
    assert ma.check_dmarc("other.org", spf_pass, [], lookup)["result"] == "none"


def test_spoofed_mail_to_our_own_domain_and_dmarc_reject():
    nothing = {"dmarc": {"result": "none", "policy": "none", "aligned": {"spf": False, "dkim": False}}}
    assert "one of this server's domains" in ma.spoofed(nothing, "kavion.ai", ["inbound.kavion.ai", "kavion.ai"])
    assert ma.spoofed(nothing, "client.org", ["kavion.ai"]) is None
    ok = {"dmarc": {"result": "pass", "policy": "reject", "aligned": {"spf": True, "dkim": False}}}
    assert ma.spoofed(ok, "kavion.ai", ["kavion.ai"]) is None
    reject = {"dmarc": {"result": "fail", "policy": "reject", "aligned": {"spf": False, "dkim": False}}}
    assert "DMARC failed" in ma.spoofed(reject, "client.org", ["kavion.ai"])


def test_authenticate_summary():
    raw = SIGNED["relaxed"].encode()
    records = {**KEY_DNS, **SPF, ("_dmarc.client.org", "TXT"): ["v=DMARC1; p=quarantine"]}
    auth = ma.authenticate(raw, {"remote_ip": "203.0.113.99", "mail_from": "ann@client.org", "helo": "x"},
                           "client.org", dns(records))
    assert auth["spf"]["result"] == "fail" and auth["dkim"][0]["result"] == "pass"
    assert auth["dmarc"]["result"] == "pass" and auth["dmarc"]["aligned"] == {"spf": False, "dkim": True}
    assert auth["summary"] == "spf=fail(client.org) dkim=pass(client.org) dmarc=pass(p=quarantine)"


def test_dns_client_parses_answers():
    import struct

    def answer(qid, rtype, rdata_list):
        msg = struct.pack(">HHHHHH", qid, 0x8180, 1, len(rdata_list), 0, 0) + ma._encode_name("example.com") + \
            struct.pack(">HH", rtype, 1)
        for rdata in rdata_list:
            msg += b"\xc0\x0c" + struct.pack(">HHIH", rtype, 1, 300, len(rdata)) + rdata
        return msg

    txt = answer(7, 16, [b"\x05v=spf\x041 -a"])
    assert ma._parse(txt, 7, 16) == (["v=spf1 -a"], False)
    mx = answer(8, 15, [struct.pack(">H", 10) + b"\x02mx\xc0\x0c"])
    assert ma._parse(mx, 8, 15)[0] == ["10 mx.example.com"]
    a = answer(9, 1, [bytes([203, 0, 113, 5])])
    assert ma._parse(a, 9, 1)[0] == ["203.0.113.5"]
    nx = struct.pack(">HHHHHH", 10, 0x8183, 1, 0, 0, 0) + ma._encode_name("x.example") + struct.pack(">HH", 1, 1)
    assert ma._parse(nx, 10, 1) == ([], False)
    with pytest.raises(ma.DnsError):
        ma._parse(answer(11, 1, []), 12, 1)
