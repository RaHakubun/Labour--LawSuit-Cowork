from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Callable


MONTHLY_PAID_DAYS = Decimal("21.75")
MONTHLY_WORK_DAYS = Decimal("20.67")
HOURS_PER_DAY = Decimal("8")
DAYS_PER_YEAR = Decimal("365")
MONEY_Q = Decimal("0.01")
YEAR_Q = Decimal("0.0001")


class CalculatorInputError(ValueError):
    """Raised when calculator inputs are invalid."""


def _to_decimal(value: Any, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CalculatorInputError(f"{name} must be a valid number") from exc


def _must_non_negative(value: Decimal, name: str) -> Decimal:
    if value < 0:
        raise CalculatorInputError(f"{name} must be >= 0")
    return value


def _q2(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _qy(value: Decimal) -> Decimal:
    return value.quantize(YEAR_Q, rounding=ROUND_HALF_UP)


def _d2f(value: Decimal) -> float:
    return float(_q2(value))


def _parse_date(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise CalculatorInputError(f"{name} must be in YYYY-MM-DD format") from exc
    raise CalculatorInputError(f"{name} must be date or YYYY-MM-DD string")


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


def _add_months(dt: date, months: int) -> date:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, _last_day_of_month(year, month))
    return date(year, month, day)


def _months_and_remaining_days(start_date: date, end_date: date) -> tuple[int, int]:
    """
    Returns full natural months and remaining days between [start_date, end_date).
    """
    if end_date <= start_date:
        return 0, 0

    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if end_date.day < start_date.day:
        months -= 1
    months = max(months, 0)

    anchor = _add_months(start_date, months)
    remaining_days = max((end_date - anchor).days, 0)
    return months, remaining_days


def _years_between(start_date: date, end_date: date) -> Decimal:
    if end_date < start_date:
        raise CalculatorInputError("end_date must be >= start_date")
    days = Decimal(str((end_date - start_date).days))
    return _qy(days / DAYS_PER_YEAR)


def _resolve_years(
    *,
    years_value: Any | None,
    start_date_value: Any | None,
    end_date_value: Any | None,
    years_name: str,
    start_name: str,
    end_name: str,
) -> Decimal:
    if years_value is not None:
        years = _to_decimal(years_value, years_name)
        return _must_non_negative(years, years_name)

    if start_date_value is None or end_date_value is None:
        raise CalculatorInputError(
            f"either {years_name} or both {start_name} and {end_name} are required"
        )

    start_date = _parse_date(start_date_value, start_name)
    end_date = _parse_date(end_date_value, end_name)
    return _years_between(start_date, end_date)


def _service_n_from_years(service_years: Decimal) -> Decimal:
    service_years = _must_non_negative(service_years, "service_years")
    full_years = int(service_years)
    remainder = service_years - Decimal(full_years)
    if remainder >= Decimal("0.5"):
        return Decimal(full_years + 1)
    if remainder > 0:
        return Decimal(full_years) + Decimal("0.5")
    return Decimal(full_years)


def _service_n_from_dates(start_date: date, end_date: date) -> Decimal:
    if end_date < start_date:
        raise CalculatorInputError("end_date must be >= start_date")
    months, remaining_days = _months_and_remaining_days(start_date, end_date)
    full_years = months // 12
    remaining_months = months % 12

    n = Decimal(full_years)
    if remaining_months >= 6:
        n += Decimal(1)
    elif remaining_months > 0 or remaining_days > 0:
        n += Decimal("0.5")
    return n


def _annual_leave_days(total_work_years: Decimal) -> int:
    total_work_years = _must_non_negative(total_work_years, "total_work_years")
    if total_work_years < 1:
        return 0
    if total_work_years < 10:
        return 5
    if total_work_years < 20:
        return 10
    return 15


@dataclass
class LabourCalculatorEngine:
    """Deterministic labour-law calculator for rule-based computations."""

    def calculate(self, calc_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "wage_base": self.calculate_wage_base,
            "overtime": self.calculate_overtime,
            "severance": self.calculate_severance,
            "medical_period": self.calculate_medical_period,
            "annual_leave_unused": self.calculate_annual_leave_unused,
            "double_wage_unsigned_contract": self.calculate_double_wage_unsigned_contract,
        }

        handler = handlers.get(calc_type)
        if handler is None:
            allowed = ", ".join(sorted(handlers.keys()))
            return {
                "ok": False,
                "result": {},
                "breakdown": {},
                "inputs": payload,
                "rules_applied": [],
                "errors": [f"unsupported calc_type: {calc_type}. allowed: {allowed}"],
            }

        try:
            body = handler(payload)
        except Exception as exc:
            return {
                "ok": False,
                "result": {},
                "breakdown": {},
                "inputs": payload,
                "rules_applied": [],
                "errors": [str(exc)],
            }
        body["ok"] = True
        body["errors"] = []
        return body

    def calculate_wage_base(self, payload: dict[str, Any]) -> dict[str, Any]:
        monthly_wage = _must_non_negative(_to_decimal(payload.get("monthly_wage"), "monthly_wage"), "monthly_wage")

        daily_wage = monthly_wage / MONTHLY_PAID_DAYS
        hourly_wage = daily_wage / HOURS_PER_DAY

        return {
            "result": {
                "daily_wage": _d2f(daily_wage),
                "hourly_wage": _d2f(hourly_wage),
            },
            "breakdown": {
                "monthly_wage": _d2f(monthly_wage),
                "monthly_paid_days": float(MONTHLY_PAID_DAYS),
                "monthly_work_days": float(MONTHLY_WORK_DAYS),
                "hours_per_day": float(HOURS_PER_DAY),
            },
            "inputs": {"monthly_wage": _d2f(monthly_wage)},
            "rules_applied": ["wage_base_21_75", "wage_base_20_67"],
        }

    def calculate_overtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        monthly_base_salary = _must_non_negative(
            _to_decimal(payload.get("monthly_base_salary"), "monthly_base_salary"),
            "monthly_base_salary",
        )
        overtime_base_salary = payload.get("overtime_base_salary")
        overtime_base = (
            _must_non_negative(_to_decimal(overtime_base_salary, "overtime_base_salary"), "overtime_base_salary")
            if overtime_base_salary is not None
            else monthly_base_salary
        )

        weekday_hours = _must_non_negative(
            _to_decimal(payload.get("weekday_overtime_hours", 0), "weekday_overtime_hours"),
            "weekday_overtime_hours",
        )
        restday_hours = _must_non_negative(
            _to_decimal(payload.get("restday_overtime_hours", 0), "restday_overtime_hours"),
            "restday_overtime_hours",
        )
        holiday_hours = _must_non_negative(
            _to_decimal(payload.get("holiday_overtime_hours", 0), "holiday_overtime_hours"),
            "holiday_overtime_hours",
        )

        hourly_base = overtime_base / MONTHLY_PAID_DAYS / HOURS_PER_DAY
        weekday_pay = hourly_base * weekday_hours * Decimal("1.5")
        restday_pay = hourly_base * restday_hours * Decimal("2")
        holiday_pay = hourly_base * holiday_hours * Decimal("3")
        total = weekday_pay + restday_pay + holiday_pay

        return {
            "result": {
                "total_overtime_pay": _d2f(total),
            },
            "breakdown": {
                "hourly_base": _d2f(hourly_base),
                "weekday_overtime_pay": _d2f(weekday_pay),
                "restday_overtime_pay": _d2f(restday_pay),
                "holiday_overtime_pay": _d2f(holiday_pay),
                "weekday_overtime_hours": float(weekday_hours),
                "restday_overtime_hours": float(restday_hours),
                "holiday_overtime_hours": float(holiday_hours),
                "overtime_base_salary": _d2f(overtime_base),
            },
            "inputs": {
                "monthly_base_salary": _d2f(monthly_base_salary),
                "overtime_base_salary": _d2f(overtime_base),
                "weekday_overtime_hours": float(weekday_hours),
                "restday_overtime_hours": float(restday_hours),
                "holiday_overtime_hours": float(holiday_hours),
            },
            "rules_applied": [
                "overtime_weekday_150pct",
                "overtime_restday_200pct",
                "overtime_holiday_300pct",
            ],
        }

    def calculate_severance(self, payload: dict[str, Any]) -> dict[str, Any]:
        monthly_avg_wage_12m = _must_non_negative(
            _to_decimal(payload.get("monthly_avg_wage_12m"), "monthly_avg_wage_12m"),
            "monthly_avg_wage_12m",
        )
        local_avg_monthly_wage = payload.get("local_avg_monthly_wage")

        if payload.get("service_years") is not None:
            raw_n = _service_n_from_years(
                _to_decimal(payload.get("service_years"), "service_years")
            )
        else:
            start_date = _parse_date(payload.get("start_date"), "start_date")
            end_date = _parse_date(payload.get("end_date"), "end_date")
            raw_n = _service_n_from_dates(start_date, end_date)

        capped_n = min(raw_n, Decimal("12"))
        wage_cap = None
        if local_avg_monthly_wage is not None:
            local_avg = _must_non_negative(
                _to_decimal(local_avg_monthly_wage, "local_avg_monthly_wage"),
                "local_avg_monthly_wage",
            )
            wage_cap = local_avg * Decimal("3")
            wage_base = min(monthly_avg_wage_12m, wage_cap)
        else:
            wage_base = monthly_avg_wage_12m

        mode = str(payload.get("mode", "")).strip().lower()
        if not mode:
            if payload.get("illegal_termination") is True:
                mode = "illegal_termination"
            elif payload.get("notice_substitute") is True:
                mode = "n_plus_1"
            else:
                mode = "normal"
        if mode not in {"normal", "n_plus_1", "illegal_termination"}:
            raise CalculatorInputError(
                "mode must be one of: normal, n_plus_1, illegal_termination"
            )

        if mode == "normal":
            payable_months = capped_n
            rule = "severance_n"
        elif mode == "n_plus_1":
            payable_months = capped_n + Decimal("1")
            rule = "severance_n_plus_1"
        else:
            payable_months = capped_n * Decimal("2")
            rule = "severance_2n"

        total = wage_base * payable_months
        rules = [rule, "severance_n_cap_12"]
        if wage_cap is not None:
            rules.append("severance_wage_cap_3x_local_avg")

        return {
            "result": {"severance_amount": _d2f(total)},
            "breakdown": {
                "raw_n": float(raw_n),
                "capped_n": float(capped_n),
                "payable_months": float(payable_months),
                "wage_base": _d2f(wage_base),
                "monthly_avg_wage_12m": _d2f(monthly_avg_wage_12m),
                "mode": mode,
            },
            "inputs": payload,
            "rules_applied": rules,
        }

    def calculate_medical_period(self, payload: dict[str, Any]) -> dict[str, Any]:
        is_shanghai = bool(payload.get("is_shanghai", False))
        total_work_years = _resolve_years(
            years_value=payload.get("total_work_years"),
            start_date_value=payload.get("first_work_date"),
            end_date_value=payload.get("reference_date"),
            years_name="total_work_years",
            start_name="first_work_date",
            end_name="reference_date",
        )
        current_company_years = _resolve_years(
            years_value=payload.get("current_company_years"),
            start_date_value=payload.get("current_company_start_date"),
            end_date_value=payload.get("reference_date"),
            years_name="current_company_years",
            start_name="current_company_start_date",
            end_name="reference_date",
        )

        if is_shanghai:
            if current_company_years < 1:
                medical_months = 3
            else:
                medical_months = min(int(current_company_years) + 2, 24)
            cycle_months = None
            rules = ["medical_period_shanghai_rule"]
        else:
            t = total_work_years
            c = current_company_years
            if t < 10:
                if c < 5:
                    medical_months, cycle_months = 3, 6
                else:
                    medical_months, cycle_months = 6, 12
            else:
                if c < 5:
                    medical_months, cycle_months = 6, 12
                elif c < 10:
                    medical_months, cycle_months = 9, 15
                elif c < 15:
                    medical_months, cycle_months = 12, 18
                elif c < 20:
                    medical_months, cycle_months = 18, 24
                else:
                    medical_months, cycle_months = 24, 30
            rules = ["medical_period_national_table"]

        result = {"medical_period_months": medical_months}
        if cycle_months is not None:
            result["accumulation_cycle_months"] = cycle_months

        return {
            "result": result,
            "breakdown": {
                "is_shanghai": is_shanghai,
                "total_work_years": float(_qy(total_work_years)),
                "current_company_years": float(_qy(current_company_years)),
                "accumulation_cycle_months": cycle_months,
            },
            "inputs": payload,
            "rules_applied": rules,
        }

    def calculate_annual_leave_unused(self, payload: dict[str, Any]) -> dict[str, Any]:
        monthly_wage = _must_non_negative(
            _to_decimal(payload.get("monthly_wage"), "monthly_wage"),
            "monthly_wage",
        )
        total_work_years = _resolve_years(
            years_value=payload.get("total_work_years"),
            start_date_value=payload.get("first_work_date"),
            end_date_value=payload.get("reference_date"),
            years_name="total_work_years",
            start_name="first_work_date",
            end_name="reference_date",
        )
        taken_leave_days = _must_non_negative(
            _to_decimal(payload.get("taken_leave_days", 0), "taken_leave_days"),
            "taken_leave_days",
        )
        if taken_leave_days != taken_leave_days.to_integral_value():
            raise CalculatorInputError("taken_leave_days must be an integer")

        annual_days = _annual_leave_days(total_work_years)
        is_current_year_join_or_leave = bool(payload.get("is_current_year_join_or_leave", False))

        if is_current_year_join_or_leave:
            current_year_days_in_company = _must_non_negative(
                _to_decimal(payload.get("current_year_days_in_company"), "current_year_days_in_company"),
                "current_year_days_in_company",
            )
            prorated_days = int((current_year_days_in_company / DAYS_PER_YEAR * Decimal(annual_days)).to_integral_value(rounding="ROUND_FLOOR"))
            unused_days = max(Decimal(prorated_days) - taken_leave_days, Decimal(0))
            rules = ["annual_leave_prorated_current_year", "annual_leave_pay_200pct"]
        else:
            current_year_days_in_company = None
            prorated_days = annual_days
            unused_days = max(Decimal(annual_days) - taken_leave_days, Decimal(0))
            rules = ["annual_leave_full_year", "annual_leave_pay_200pct"]

        extra_pay = (monthly_wage / MONTHLY_PAID_DAYS) * unused_days * Decimal("2")

        breakdown: dict[str, Any] = {
            "annual_leave_days": annual_days,
            "prorated_annual_leave_days": prorated_days,
            "taken_leave_days": int(taken_leave_days),
            "unused_leave_days": float(unused_days),
            "daily_wage": _d2f(monthly_wage / MONTHLY_PAID_DAYS),
        }
        if current_year_days_in_company is not None:
            breakdown["current_year_days_in_company"] = float(current_year_days_in_company)

        return {
            "result": {"unused_annual_leave_extra_pay": _d2f(extra_pay)},
            "breakdown": breakdown,
            "inputs": payload,
            "rules_applied": rules,
        }

    def calculate_double_wage_unsigned_contract(self, payload: dict[str, Any]) -> dict[str, Any]:
        entry_date = _parse_date(payload.get("entry_date"), "entry_date")
        unsigned_end_date = _parse_date(payload.get("unsigned_end_date"), "unsigned_end_date")
        monthly_wage = _must_non_negative(
            _to_decimal(payload.get("monthly_wage"), "monthly_wage"),
            "monthly_wage",
        )

        unsigned_start_date = _add_months(entry_date, 1)
        if unsigned_end_date <= unsigned_start_date:
            return {
                "result": {"double_wage_gap": 0.0},
                "breakdown": {
                    "unsigned_start_date": unsigned_start_date.isoformat(),
                    "unsigned_end_date": unsigned_end_date.isoformat(),
                    "effective_months": 0.0,
                    "compensable_months": 0.0,
                    "remaining_days": 0,
                },
                "inputs": payload,
                "rules_applied": ["double_wage_no_effective_period"],
            }

        full_months, remaining_days = _months_and_remaining_days(unsigned_start_date, unsigned_end_date)
        effective_months = Decimal(full_months) + (Decimal(remaining_days) / MONTHLY_WORK_DAYS)

        if effective_months < 1:
            compensable_months = effective_months
            rules = ["double_wage_sub_one_month_by_days"]
        elif effective_months < 12:
            compensable_months = max(effective_months - Decimal("1"), Decimal(0))
            rules = ["double_wage_under_12_months_minus_1"]
        else:
            compensable_months = Decimal("11")
            rules = ["double_wage_cap_11_months"]

        amount = monthly_wage * compensable_months
        return {
            "result": {"double_wage_gap": _d2f(amount)},
            "breakdown": {
                "unsigned_start_date": unsigned_start_date.isoformat(),
                "unsigned_end_date": unsigned_end_date.isoformat(),
                "full_natural_months": full_months,
                "remaining_days": remaining_days,
                "effective_months": float(_qy(effective_months)),
                "compensable_months": float(_qy(compensable_months)),
                "monthly_wage": _d2f(monthly_wage),
            },
            "inputs": payload,
            "rules_applied": rules,
        }

