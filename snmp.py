"""A small SNMP v2c client: GETBULK walks over UDP, nothing else.

Floorbit installs no SNMP library and a plugin can't add one, so this speaks the protocol itself.
It only reads, and only what a walk of a few AireOS tables needs: INTEGER, OCTET STRING, OID and
the unsigned types in, endOfMibView and errors out.
"""
import os
import socket

SEQUENCE, INTEGER, OCTETS, NULL, OID = 0x30, 0x02, 0x04, 0x05, 0x06
GET_BULK, RESPONSE = 0xA5, 0xA2
UNSIGNED = {0x41, 0x42, 0x43, 0x46}         # Counter32, Gauge32, TimeTicks, Counter64
END_TAGS = {0x80, 0x81, 0x82}               # noSuchObject, noSuchInstance, endOfMibView
IP_ADDRESS = 0x40


class SnmpError(RuntimeError):
    pass


def _length(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag, value):
    return bytes([tag]) + _length(len(value)) + value


def enc_int(n):
    return tlv(INTEGER, n.to_bytes(max(1, (n.bit_length() + 8) // 8), 'big', signed=True))


def enc_oid(oid):
    parts = [int(p) for p in oid.strip('.').split('.')]
    out = bytearray([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return tlv(OID, bytes(out))


def dec_oid(raw):
    parts = [raw[0] // 40, raw[0] % 40] if raw else []
    n = 0
    for b in raw[1:]:
        n = (n << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(n)
            n = 0
    return '.'.join(map(str, parts))


def read(buf, at=0):
    """(tag, value bytes, next offset)."""
    if at + 2 > len(buf):
        raise SnmpError('The SNMP answer is cut short.')
    tag, n = buf[at], buf[at + 1]
    at += 2
    if n & 0x80:
        size = n & 0x7F
        n = int.from_bytes(buf[at:at + size], 'big')
        at += size
    if at + n > len(buf):
        raise SnmpError('The SNMP answer is cut short.')
    return tag, buf[at:at + n], at + n


def items(buf):
    at = 0
    while at < len(buf):
        tag, value, at = read(buf, at)
        yield tag, value


def value_of(tag, raw):
    if tag == INTEGER:
        return int.from_bytes(raw, 'big', signed=True)
    if tag in UNSIGNED:
        return int.from_bytes(raw, 'big')
    if tag == OCTETS:
        return raw
    if tag == OID:
        return dec_oid(raw)
    if tag == IP_ADDRESS:
        return '.'.join(map(str, raw))
    return None


def bulk_request(community, request_id, oid, repetitions):
    varbind = tlv(SEQUENCE, enc_oid(oid) + tlv(NULL, b''))
    pdu = tlv(GET_BULK, enc_int(request_id) + enc_int(0) + enc_int(repetitions) + tlv(SEQUENCE, varbind))
    return tlv(SEQUENCE, enc_int(1) + tlv(OCTETS, community.encode()) + pdu)


def parse_response(buf, request_id):
    """[(oid, tag, raw value)] from a RESPONSE PDU."""
    tag, message, _ = read(buf)
    if tag != SEQUENCE:
        raise SnmpError('That is not an SNMP answer.')
    parts = list(items(message))
    if len(parts) < 3 or parts[2][0] != RESPONSE:
        raise SnmpError('That is not an SNMP answer.')
    pdu = list(items(parts[2][1]))
    if value_of(*pdu[0]) != request_id:
        return None
    status = value_of(*pdu[1])
    if status:
        raise SnmpError(f'The controller answered SNMP error {status}.')
    out = []
    for _tag, varbind in items(pdu[3][1]):
        (_, oid_raw), (vtag, vraw) = list(items(varbind))[:2]
        out.append((dec_oid(oid_raw), vtag, vraw))
    return out


def walk(host, community, base, *, port=161, timeout=3.0, retries=1, repetitions=25, limit=20000):
    """{oid suffix after base: value} for every object under base."""
    base = base.strip('.')
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    found, at = {}, base
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        while len(found) < limit:
            request_id = int.from_bytes(os.urandom(3), 'big')
            packet = bulk_request(community, request_id, at, repetitions)
            rows = None
            for _ in range(retries + 1):
                sock.sendto(packet, (host, port))
                try:
                    while rows is None:
                        rows = parse_response(sock.recv(65535), request_id)
                    break
                except TimeoutError:
                    continue
            if rows is None:
                raise SnmpError(f'No SNMP answer from {host} (wrong community, or UDP {port} blocked?).')
            done = not rows
            for oid, tag, raw in rows:
                if tag in END_TAGS or not oid.startswith(base + '.'):
                    done = True
                    break
                found[oid[len(base) + 1:]] = value_of(tag, raw)
                at = oid
            if done:
                break
    return found
