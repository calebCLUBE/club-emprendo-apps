from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .models import (
    ApplicationGradingConfig,
    FormDefinition,
    FormGroup,
    PairingConfig,
)


DEFAULT_E_WEIGHTS = {
    "prior_mentoring": 2,
    "business_age": 5,
    "has_employees": 2,
    "business_description": 3,
    "growth_how": 4,
    "biggest_challenge": 4,
}

DEFAULT_M_WEIGHTS = {
    "owned_business": 3,
    "business_years": 4,
    "has_employees": 2,
    "professional_expertise_struct": 2,
    "mentoring_exp_as_mentor": 3,
    "mentoring_exp_as_student": 2,
    "business_description": 3,
    "mentoring_exp_detail": 2,
    "motivation": 4,
    "professional_expertise": 4,
}

DEFAULT_E_MAX_TOTAL = 64
DEFAULT_M_MAX_TOTAL = 81

DEFAULT_PAIRING_PRIORITY_RULES = [
    {
        "label": "availability",
        "emprendedora_question_slug": "preferred_schedule",
        "mentora_question_slug": "availability_grid",
        "comparison_type": "availability_overlap",
        "weight": 10,
        "required": True,
        "output_key": "availability",
        "position": 10,
    },
    {
        "label": "industry",
        "emprendedora_question_slug": "industry",
        "mentora_question_slug": "business_industry",
        "comparison_type": "exact",
        "weight": 30,
        "required": False,
        "output_key": "industry",
        "position": 20,
    },
    {
        "label": "country",
        "emprendedora_question_slug": "country_residence",
        "mentora_question_slug": "country_residence",
        "comparison_type": "exact",
        "weight": 20,
        "required": False,
        "output_key": "country",
        "position": 30,
    },
    {
        "label": "business age",
        "emprendedora_question_slug": "business_age",
        "mentora_question_slug": "business_years",
        "comparison_type": "business_age",
        "weight": 10,
        "required": False,
        "output_key": "biz_age",
        "position": 40,
    },
]

DEFAULT_PAIRING_AI_COMPARISONS = [
    {
        "label": "expertise vs growth plan",
        "emprendedora_question_slug": "growth_how",
        "mentora_question_slug": "professional_expertise",
        "weight": 6,
        "output_key": "llm1",
        "position": 10,
    },
    {
        "label": "motivation vs challenge",
        "emprendedora_question_slug": "biggest_challenge",
        "mentora_question_slug": "motivation",
        "weight": 6,
        "output_key": "llm2",
        "position": 20,
    },
]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _track_from_form_slug(form_slug: str) -> str:
    slug = (form_slug or "").upper()
    if slug.endswith("M_A2") or slug.endswith("M_A1"):
        return "M"
    return "E"


def default_weights_for_track(track: str) -> dict[str, float]:
    source = DEFAULT_M_WEIGHTS if (track or "").upper() == "M" else DEFAULT_E_WEIGHTS
    return {key: float(value) for key, value in source.items()}


def default_max_total_for_track(track: str) -> float:
    return float(DEFAULT_M_MAX_TOTAL if (track or "").upper() == "M" else DEFAULT_E_MAX_TOTAL)


@dataclass
class RuntimeGradingConfig:
    form_slug: str
    weights: dict[str, float]
    prompts: dict[str, str]
    negative_allowed: set[str]
    max_total_score: float
    model_name: str
    rubric_note: str

    def weight(self, key: str, default: float = 0.0) -> float:
        return _to_float(self.weights.get(key), default)

    def prompt(self, key: str) -> str:
        return self.prompts.get(key, "")

    def allows_negative(self, key: str, fallback: bool = False) -> bool:
        return key in self.negative_allowed or fallback


def runtime_grading_config_for_form_slug(form_slug: str) -> RuntimeGradingConfig:
    track = _track_from_form_slug(form_slug)
    weights = default_weights_for_track(track)
    max_total = default_max_total_for_track(track)
    prompts: dict[str, str] = {}
    negative_allowed: set[str] = set()
    model_name = ""
    rubric_note = ""

    config = (
        ApplicationGradingConfig.objects
        .filter(form__slug=form_slug)
        .prefetch_related("criteria")
        .first()
    )
    if config:
        model_name = (config.model_name or "").strip()
        rubric_note = (config.rubric_note or "").strip()
        if config.max_total_score is not None:
            max_total = _to_float(config.max_total_score, max_total)
        for criterion in config.criteria.all():
            if not criterion.active:
                continue
            key = (criterion.question_slug or "").strip()
            if not key:
                continue
            weights[key] = _to_float(criterion.weight, weights.get(key, 0.0))
            if criterion.prompt:
                prompts[key] = criterion.prompt
            if criterion.negative_allowed:
                negative_allowed.add(key)

    return RuntimeGradingConfig(
        form_slug=form_slug,
        weights=weights,
        prompts=prompts,
        negative_allowed=negative_allowed,
        max_total_score=max_total,
        model_name=model_name,
        rubric_note=rubric_note,
    )


@dataclass
class RuntimePairingConfig:
    group_number: int
    top_k_for_ai: int
    availability_required: bool
    model_name: str
    priority_rules: list[dict[str, Any]]
    ai_comparisons: list[dict[str, Any]]


def runtime_pairing_config_for_group(group_num: int) -> RuntimePairingConfig:
    priority_rules = [dict(item) for item in DEFAULT_PAIRING_PRIORITY_RULES]
    ai_comparisons = [dict(item) for item in DEFAULT_PAIRING_AI_COMPARISONS]
    top_k_for_ai = 3
    availability_required = True
    model_name = ""

    config = (
        PairingConfig.objects
        .filter(group__number=group_num)
        .prefetch_related("priority_rules", "ai_comparisons")
        .first()
    )
    if config:
        top_k_for_ai = max(1, int(config.top_k_for_ai or 3))
        availability_required = bool(config.availability_required)
        model_name = (config.model_name or "").strip()
        configured_rules = [
            {
                "label": rule.label,
                "emprendedora_question_slug": rule.emprendedora_question_slug,
                "mentora_question_slug": rule.mentora_question_slug,
                "comparison_type": rule.comparison_type,
                "weight": _to_float(rule.weight, 0.0),
                "required": bool(rule.required),
                "output_key": rule.output_key or rule.label,
                "position": rule.position,
            }
            for rule in config.priority_rules.all()
            if rule.active
        ]
        configured_ai = [
            {
                "label": item.label,
                "emprendedora_question_slug": item.emprendedora_question_slug,
                "mentora_question_slug": item.mentora_question_slug,
                "weight": _to_float(item.weight, 0.0),
                "prompt": item.prompt,
                "output_key": item.output_key or item.label,
                "position": item.position,
            }
            for item in config.ai_comparisons.all()
            if item.active
        ]
        if configured_rules:
            priority_rules = sorted(configured_rules, key=lambda x: (x.get("position") or 0, x.get("label") or ""))
        if configured_ai:
            ai_comparisons = sorted(configured_ai, key=lambda x: (x.get("position") or 0, x.get("label") or ""))

    return RuntimePairingConfig(
        group_number=int(group_num),
        top_k_for_ai=top_k_for_ai,
        availability_required=availability_required,
        model_name=model_name,
        priority_rules=priority_rules,
        ai_comparisons=ai_comparisons,
    )


def ensure_grading_config_for_form(form: FormDefinition) -> ApplicationGradingConfig:
    config, _ = ApplicationGradingConfig.objects.get_or_create(form=form)
    track = _track_from_form_slug(form.slug)
    defaults = default_weights_for_track(track)
    if config.max_total_score is None:
        config.max_total_score = Decimal(str(default_max_total_for_track(track)))
        config.save(update_fields=["max_total_score", "updated_at"])

    existing = set(config.criteria.values_list("question_slug", flat=True))
    ai_defaults = {
        "business_description",
        "growth_how",
        "biggest_challenge",
        "mentoring_exp_detail",
        "motivation",
        "professional_expertise",
    }
    position = 10
    for slug, weight in defaults.items():
        if slug in existing:
            continue
        config.criteria.create(
            question_slug=slug,
            label=slug.replace("_", " ").title(),
            criterion_type="ai_text" if slug in ai_defaults else "structured",
            weight=Decimal(str(weight)),
            negative_allowed=(slug == "motivation" and track == "M"),
            position=position,
        )
        position += 10
    return config


def ensure_pairing_config_for_group(group: FormGroup) -> PairingConfig:
    config, _ = PairingConfig.objects.get_or_create(group=group)
    if not config.priority_rules.exists():
        for item in DEFAULT_PAIRING_PRIORITY_RULES:
            config.priority_rules.create(**item)
    if not config.ai_comparisons.exists():
        for item in DEFAULT_PAIRING_AI_COMPARISONS:
            config.ai_comparisons.create(**item)
    return config
