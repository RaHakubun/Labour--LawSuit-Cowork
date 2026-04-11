from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .city_wage_dataset import (
    CITY_WAGE_DATA,
    PROVINCE_COUNT,
    RECORD_COUNT,
    SOURCE_FILE,
    SOURCE_SHA256,
    UPDATED_AT_UTC,
)


_PROVINCE_SUFFIXES = (
    "壮族自治区",
    "回族自治区",
    "维吾尔自治区",
    "自治区",
    "特别行政区",
    "省",
    "市",
)
_CITY_SUFFIXES = ("自治州", "地区", "盟", "州", "市")
_DIRECT_MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}


@dataclass(frozen=True)
class CityWageRecord:
    province: str
    city: str
    avg_wage: Decimal


def _normalize_text(text: str) -> str:
    return str(text).strip().replace(" ", "").replace("\u3000", "")


def _strip_suffixes(name: str, suffixes: Iterable[str]) -> str:
    out = name
    for s in suffixes:
        if out.endswith(s) and len(out) > len(s):
            out = out[: -len(s)]
            break
    return out


def _province_variants(name: str) -> set[str]:
    norm = _normalize_text(name)
    if not norm:
        return set()
    variants = {norm}
    stripped = _strip_suffixes(norm, _PROVINCE_SUFFIXES)
    variants.add(stripped)
    if stripped in _DIRECT_MUNICIPALITIES:
        variants.add(f"{stripped}市")
    # keep only non-empty
    return {v for v in variants if v}


def _city_variants(name: str) -> set[str]:
    norm = _normalize_text(name)
    if not norm:
        return set()
    variants = {norm}
    variants.add(_strip_suffixes(norm, _CITY_SUFFIXES))
    return {v for v in variants if v}


def _build_province_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for province in CITY_WAGE_DATA.keys():
        for alias in _province_variants(province):
            aliases.setdefault(alias, province)
    return aliases


def _build_city_aliases() -> dict[str, dict[str, str]]:
    by_province: dict[str, dict[str, str]] = {}
    for province, city_map in CITY_WAGE_DATA.items():
        aliases: dict[str, str] = {}
        for city in city_map.keys():
            for alias in _city_variants(city):
                aliases.setdefault(alias, city)
        by_province[province] = aliases
    return by_province


_PROVINCE_ALIASES = _build_province_aliases()
_CITY_ALIASES_BY_PROVINCE = _build_city_aliases()


def dataset_meta() -> dict[str, str | int]:
    return {
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "updated_at_utc": UPDATED_AT_UTC,
        "record_count": RECORD_COUNT,
        "province_count": PROVINCE_COUNT,
    }


def list_provinces() -> list[str]:
    return list(CITY_WAGE_DATA.keys())


def resolve_province(province: str) -> str:
    key = _normalize_text(province)
    if not key:
        raise ValueError("province is required")

    canonical = _PROVINCE_ALIASES.get(key)
    if canonical:
        return canonical

    for variant in _province_variants(key):
        canonical = _PROVINCE_ALIASES.get(variant)
        if canonical:
            return canonical
    raise ValueError(f"unknown province: {province}")


def list_cities(province: str) -> list[str]:
    canonical_province = resolve_province(province)
    return list(CITY_WAGE_DATA[canonical_province].keys())


def resolve_city(province: str, city: str | None = None) -> str:
    canonical_province = resolve_province(province)
    city_map = CITY_WAGE_DATA[canonical_province]

    if city is None or not _normalize_text(city):
        # direct municipality rows use city same as province name.
        municipality_city = _strip_suffixes(canonical_province, _PROVINCE_SUFFIXES)
        if municipality_city in city_map:
            return municipality_city
        if canonical_province in city_map:
            return canonical_province
        raise ValueError(f"city is required for province: {canonical_province}")

    key = _normalize_text(city)
    aliases = _CITY_ALIASES_BY_PROVINCE[canonical_province]
    canonical_city = aliases.get(key)
    if canonical_city:
        return canonical_city

    for variant in _city_variants(key):
        canonical_city = aliases.get(variant)
        if canonical_city:
            return canonical_city
    raise ValueError(f"unknown city under province {canonical_province}: {city}")


def resolve_city_wage(province: str, city: str | None = None) -> CityWageRecord:
    canonical_province = resolve_province(province)
    canonical_city = resolve_city(canonical_province, city)
    raw = CITY_WAGE_DATA[canonical_province][canonical_city]
    try:
        avg_wage = Decimal(str(raw))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(
            f"invalid wage value in dataset: province={canonical_province}, city={canonical_city}, wage={raw}"
        ) from exc
    return CityWageRecord(
        province=canonical_province,
        city=canonical_city,
        avg_wage=avg_wage,
    )

