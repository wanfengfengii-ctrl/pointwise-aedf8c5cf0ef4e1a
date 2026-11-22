import hmac
import json
from dataclasses import dataclass

from day.calendar import StationDay, check_name, check_station_day, parse_station_day
from day.store import decode_int, encode_int


@dataclass(frozen=True, slots=True)
class GrantDocument:
    station_day: StationDay
    grant_id: str
    hub_id: str
    branch_id: str
    rows: int
    seal: str


@dataclass(frozen=True, slots=True)
class ReturnDocument:
    grant: GrantDocument
    request_id: str
    returned_rows: int
    cumulative_rows: int
    seal: str


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'))


def _seal(key, kind, body):
    payload = _json([kind, body]).encode('ascii')
    return hmac.digest(key, payload, 'sha256').hex()


def _verify(key, kind, body, seal):
    if type(seal) is not str or len(seal) != 64 or not seal.isascii():
        raise ValueError('交接凭据签记无效')
    if not hmac.compare_digest(_seal(key, kind, body), seal):
        raise ValueError('交接凭据正文与签记不符')


def _grant_body(station_day, grant_id, hub_id, branch_id, rows):
    return [*station_day.key, grant_id, hub_id, branch_id, encode_int(rows)]


def _grant_packet(document):
    return [_grant_body(document.station_day, document.grant_id, document.hub_id,
                        document.branch_id, document.rows), document.seal]


def _read_grant(packet):
    body, seal = packet
    station, iso_day, grant_id, hub_id, branch_id, rows = body
    return GrantDocument(parse_station_day(station, iso_day), grant_id, hub_id,
                         branch_id, decode_int(rows), seal)


def _return_body(grant, request_id, returned_rows, cumulative_rows):
    return [_grant_packet(grant), request_id, encode_int(returned_rows), encode_int(cumulative_rows)]


def issue_grant_document(key, station_day, grant_id, hub_id, branch_id, rows):
    body = _grant_body(station_day, grant_id, hub_id, branch_id, rows)
    return GrantDocument(station_day, grant_id, hub_id, branch_id, rows, _seal(key, 'grant/1', body))


def issue_return_document(key, grant, request_id, returned_rows, cumulative_rows):
    body = _return_body(grant, request_id, returned_rows, cumulative_rows)
    return ReturnDocument(grant, request_id, returned_rows, cumulative_rows, _seal(key, 'return/1', body))


def validate_grant_document(document, *, hub_id, branch_id, key):
    if type(document) is not GrantDocument:
        raise ValueError('须提供划拨凭据')
    check_station_day(document.station_day)
    check_name(document.grant_id, '划拨号')
    check_name(document.hub_id, '总仓')
    check_name(document.branch_id, '分仓')
    if (document.hub_id, document.branch_id) != (hub_id, branch_id):
        raise ValueError('划拨凭据与本次交接仓库不符')
    if type(document.rows) is not int or document.rows <= 0:
        raise ValueError('划拨凭据的行额度无效')
    body, seal = _grant_packet(document)
    _verify(key, 'grant/1', body, seal)
    return document


def validate_return_document(document, *, hub_id, branch_id, key):
    if type(document) is not ReturnDocument:
        raise ValueError('须提供退额凭据')
    grant = validate_grant_document(document.grant, hub_id=hub_id, branch_id=branch_id, key=key)
    check_name(document.request_id, '退额请求号')
    returned, cumulative = document.returned_rows, document.cumulative_rows
    if type(returned) is not int or type(cumulative) is not int:
        raise ValueError('退额凭据的行额度须为整数')
    if not 0 < returned <= cumulative <= grant.rows:
        raise ValueError('退额证明范围超过划拨或无效')
    _verify(key, 'return/1', _return_body(grant, document.request_id, returned, cumulative), document.seal)
    return document


def dump_grant(document):
    return _json(_grant_packet(document))


def load_grant(text):
    return _read_grant(json.loads(text))


def dump_return(document):
    body = _return_body(document.grant, document.request_id, document.returned_rows, document.cumulative_rows)
    return _json([body, document.seal])


def load_return(text):
    body, seal = json.loads(text)
    grant, request_id, returned, cumulative = body
    return ReturnDocument(_read_grant(grant), request_id, decode_int(returned), decode_int(cumulative), seal)
