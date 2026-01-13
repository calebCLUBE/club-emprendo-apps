# applications/models.py
from django.db import models
import uuid


class FormGroup(models.Model):
    """
    Represents a cohort/group like 'Group 6', with dates used for #(month)/#(year) placeholders.
    """
    number = models.PositiveIntegerField(unique=True)
    start_month = models.CharField(max_length=30)
    end_month = models.CharField(max_length=30)
    year = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return f"Group {self.number} ({self.start_month}–{self.end_month} {self.year})"


class FormDefinition(models.Model):
    """
    Logical form (E_A1, E_A2, M_A1, M_A2) or a group copy (G6_E_A1 etc).
    """
    slug = models.SlugField(unique=True)  # e.g. "E_A1" or "G6_E_A1"
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # master vs group copy
    is_master = models.BooleanField(default=False)

    # if is_master=False, this can optionally point to the group it belongs to
    group = models.ForeignKey(
        "applications.FormGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forms",
    )

    is_public = models.BooleanField(
        default=True,
        help_text=(
            "Public forms are linked directly from the website (A1). "
            "A2 forms are private and used via invite link."
        ),
    )
    accepting_responses = models.BooleanField(default=True)



    def __str__(self) -> str:
        return self.name


class Question(models.Model):
    SHORT_TEXT = "short_text"
    LONG_TEXT = "long_text"
    INTEGER = "integer"
    BOOLEAN = "boolean"            # single checkbox
    CHOICE = "choice"              # single choice (radio/dropdown)
    MULTI_CHOICE = "multi_choice"  # multiple choice (checkbox list)

    FIELD_TYPES = [
        (SHORT_TEXT, "Short text"),
        (LONG_TEXT, "Long text"),
        (INTEGER, "Integer"),
        (BOOLEAN, "Yes/No (checkbox)"),
        (CHOICE, "Single choice"),
        (MULTI_CHOICE, "Multiple choice"),
    ]

    form = models.ForeignKey(
        FormDefinition,
        on_delete=models.CASCADE,
        related_name="questions",
    )

    text = models.CharField(max_length=255)
    help_text = models.TextField(blank=True)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES)
    required = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    slug = models.SlugField(
        help_text="Stable identifier for grading (e.g. 'm_a1_requisitos')."
    )

    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("form", "slug")
        ordering = ["position"]

    def __str__(self) -> str:
        return f"{self.form.slug} – {self.position}. {self.text}"


class Choice(models.Model):
    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="choices",
    )
    label = models.CharField(max_length=200)
    value = models.CharField(
        max_length=100,
        help_text="Machine-readable value stored in Answer.value",
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    def __str__(self) -> str:
        return f"{self.question.slug} → {self.label}"


class Application(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)

    form = models.ForeignKey(
        FormDefinition,
        on_delete=models.PROTECT,
        related_name="applications",
    )

    # These fields are OK to keep for now (even if you later remove them from the forms)
    name = models.CharField(max_length=200)
    email = models.EmailField()

    # Scores (mainly used for A2 forms)
    tablestakes_score = models.FloatField(default=0)
    commitment_score = models.FloatField(default=0)
    nice_to_have_score = models.FloatField(default=0)
    overall_score = models.FloatField(default=0)
    recommendation = models.CharField(max_length=20, blank=True)

    # For A1: token+flag to invite to A2
    invite_token = models.UUIDField(null=True, blank=True, unique=True)
    invited_to_second_stage = models.BooleanField(default=False)

    def generate_invite_token(self):
        if not self.invite_token:
            self.invite_token = uuid.uuid4()

    def __str__(self) -> str:
        return f"{self.form.slug} – {self.name} – {self.created_at:%Y-%m-%d}"


class Answer(models.Model):
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    value = models.TextField()

    def __str__(self) -> str:
        return f"{self.application.id} – {self.question.slug} = {self.value}"
