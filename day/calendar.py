from dataclasses import dataclass
from datetime import date


def check_name(value: str, label: str) -> str:
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise ValueError(f'{label}须为非空名称')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'{label}须为有效的 Unicode 文本') from error
    return value


@dataclass(frozen=True, slots=True)
class StationDay:
    station: str
    day: date

    def __post_init__(self) -> None:
        check_name(self.station, '站点')
        if type(self.day) is not date:
            raise ValueError('业务日期须为 date')

    @property
    def key(self) -> tuple[str, str]:
        return self.station, self.day.isoformat()


def parse_station_day(station: str, iso_day: str) -> StationDay:
    if type(iso_day) is not str:
        raise ValueError('业务日期须使用 YYYY-MM-DD')
    parsed = date.fromisoformat(iso_day)
    if parsed.isoformat() != iso_day:
        raise ValueError('业务日期须使用 YYYY-MM-DD')
    return StationDay(station, parsed)


def check_station_day(value: StationDay) -> StationDay:
    if type(value) is not StationDay:
        raise ValueError('须提供站日键')
    return value
