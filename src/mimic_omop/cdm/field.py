from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    datatype: str  # as written in the CDM spec: integer, float, date, datetime, varchar(n)
    required: bool = False
    pk: bool = False
    fk: tuple[str, str] | None = None  # (table, field)
