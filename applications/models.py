# applications/models.py
from django.db import models
import uuid
from .application import Application  # <-- adjust import if needed


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

class GradingJob(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    form_slug = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    log = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def append_log(self, text: str):
        self.log += text + "\n"
        self.save(update_fields=["log"])

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


class GradedFile(models.Model):
    form_slug = models.CharField(
        max_length=50,
        db_index=True,
    )

    # Nullable because this represents a FULL-FORM grading run
    # (not a single application)
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="graded_files",
    )

    csv_text = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]  # 🔑 fixes latest(), admin lists, UI display

    def __str__(self):
        return f"{self.form_slug} — graded ({self.created_at:%Y-%m-%d %H:%M})"


class GradingJob(models.Model):
    """
    Represents a background grading job (batch or single-application).
    Logs are stored directly in the database so progress can be viewed live
    from the admin UI.
    """

    # --------------------
    # Status machine
    # --------------------
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    # --------------------
    # What is being graded
    # --------------------
    form_slug = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Form slug being graded (e.g. G6_E_A2). Empty if single-app job.",
        db_index=True,
    )

    app_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Application ID if this job grades a single application.",
    )

    # --------------------
    # Job state + logs
    # --------------------
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    log_text = models.TextField(
        blank=True,
        default="",
        help_text="Append-only job log (progress, errors, tracebacks).",
    )

    # --------------------
    # Timestamps
    # --------------------
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --------------------
    # Helpers
    # --------------------
    def append_log(self, line: str) -> None:
        """
        Safely append a line to the job log.
        """
        self.log_text = (self.log_text or "") + line.rstrip() + "\n"
        self.save(update_fields=["log_text", "updated_at"])

    def __str__(self) -> str:
        target = self.form_slug or f"app {self.app_id}"
        return f"GradingJob #{self.id} [{self.status}] → {target}"