MILLILITRES_PER_LITRE = 1000


def normalize_litres(value: int | None, unit: str) -> int | None:
    if type(unit) is not str or unit not in ('L', 'ml'):
        raise ValueError('单位须为 L 或 ml')
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError('读值须为整数或 None')
    if unit == 'L':
        return value
    litres, remainder = divmod(value, MILLILITRES_PER_LITRE)
    if remainder:
        raise ValueError('毫升读值须能整除 1000')
    return litres
