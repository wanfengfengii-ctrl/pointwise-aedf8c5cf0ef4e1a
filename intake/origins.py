from dataclasses import dataclass

from day.calendar import check_name


@dataclass(frozen=True, slots=True)
class RowOrigin:
    source: str
    source_line: int
    position: int


def build_origin(source: str, source_line: int, position: int) -> RowOrigin:
    check_name(source, '报送来源')
    if type(source_line) is not int or source_line <= 0:
        raise ValueError('源行号须为正整数')
    if type(position) is not int or position <= 0:
        raise ValueError('报送内位置须为正整数')
    return RowOrigin(source, source_line, position)
