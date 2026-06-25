from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping


def is_yes_value(value: object) -> bool:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.strip().lower()
    return text in {"1", "true", "yes", "si", "on"} or text.startswith(("yes,", "si,", "yes ", "si "))


def _first_answer(answers: Mapping[str, object], slugs: Iterable[str]) -> object | None:
    for slug in slugs:
        value = answers.get(slug)
        if str(value or "").strip():
            return value
    return None


def _prefixed_answers(answers: Mapping[str, object], prefixes: Iterable[str]) -> list[object]:
    return [
        value
        for slug, value in answers.items()
        if any(slug.startswith(prefix) for prefix in prefixes) and str(value or "").strip()
    ]


def criterion_passes(
    answers: Mapping[str, object],
    *,
    aggregate_slugs: Iterable[str],
    individual_prefixes: Iterable[str],
) -> bool | None:
    aggregate = _first_answer(answers, aggregate_slugs)
    if aggregate is not None:
        return is_yes_value(aggregate)

    individual = _prefixed_answers(answers, individual_prefixes)
    if individual:
        return all(is_yes_value(value) for value in individual)

    return None


def mentor_a1_passes(answers: Mapping[str, object]) -> bool:
    requirements = criterion_passes(
        answers,
        aggregate_slugs=(
            "meets_requirements",
            "m1_meet_requirements",
            "m1_meets_requirements",
            "m1_requirements_ok",
        ),
        individual_prefixes=("req_basic_", "m1_req_basic_"),
    )
    availability = criterion_passes(
        answers,
        aggregate_slugs=(
            "available_period",
            "availability_ok",
            "m1_availability_ok",
            "m1_available_period",
            "m1_available",
        ),
        individual_prefixes=("req_avail_", "m1_req_avail_"),
    )

    recognized = [result for result in (requirements, availability) if result is not None]
    return all(recognized) if recognized else True


def entrepreneur_a1_passes(answers: Mapping[str, object]) -> bool:
    requirements = criterion_passes(
        answers,
        aggregate_slugs=("meets_requirements", "e1_meet_requirements"),
        individual_prefixes=("req_basic_", "e1_req_basic_"),
    )
    availability = criterion_passes(
        answers,
        aggregate_slugs=("available_period", "e1_available_period", "availability_ok"),
        individual_prefixes=("req_avail_", "e1_req_avail_"),
    )
    active_business = criterion_passes(
        answers,
        aggregate_slugs=("business_active", "e1_has_running_business"),
        individual_prefixes=("req_business_active", "e1_req_business_active"),
    )

    # Some current E_A1 forms include an active business in the requirements
    # declaration instead of asking it as a separate question.
    if active_business is None:
        active_business = requirements

    recognized = [
        result
        for result in (requirements, availability, active_business)
        if result is not None
    ]
    return all(recognized) if recognized else True
