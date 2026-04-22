# applications/models.py
from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid


class FormGroup(models.Model):
    """
    Represents a cohort/group like 'Group 6', with dates used for #(month)/#(year) placeholders.
    """
    number = models.PositiveIntegerField(unique=True)
    start_day = models.PositiveIntegerField(default=1)
    start_month = models.CharField(max_length=30)
    end_month = models.CharField(max_length=30)
    year = models.PositiveIntegerField()
    a2_deadline = models.DateField(null=True, blank=True, help_text="Fecha límite para completar la aplicación 2.")
    open_at = models.DateTimeField(null=True, blank=True, help_text="Fecha/hora en la que las aplicaciones del grupo se abrirán automáticamente.")
    close_at = models.DateTimeField(null=True, blank=True, help_text="Fecha/hora en la que las aplicaciones del grupo se cerrarán automáticamente.")
    reminder_1_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha/hora para el recordatorio automático #1 de A2.",
    )
    reminder_2_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha/hora para el recordatorio automático #2 de A2.",
    )
    reminder_3_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha/hora para el recordatorio automático #3 de A2.",
    )
    reminder_1_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    reminder_2_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    reminder_3_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    use_combined_application = models.BooleanField(
        default=False,
        help_text="If enabled, this group uses the combined application flow/database view.",
    )
    custom_name = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Optional custom label for this group in admin pages.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "If disabled, the group is archived/hidden in participant-management views "
            "while preserving application database records."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        label = (self.custom_name or "").strip() or f"Group {self.number}"
        return f"{label} ({self.start_day} {self.start_month}–{self.end_month} {self.year})"


def scheduled_group_open_state(group: "FormGroup", now=None) -> bool | None:
    if not group:
        return None

    open_at = getattr(group, "open_at", None)
    close_at = getattr(group, "close_at", None)
    if not open_at and not close_at:
        return None

    now = now or timezone.now()
    desired_open = True
    if open_at and now < open_at:
        desired_open = False
    if close_at and now >= close_at:
        desired_open = False
    return desired_open

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
    manual_open_override = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text="Optional manual override for open/closed state. Leave blank to follow the group schedule.",
    )
    default_section_title = models.CharField(
        max_length=200,
        default="Preguntas generales",
        help_text="Título para las preguntas sin sección asignada.",
    )


    def __str__(self) -> str:
        return self.name


class Section(models.Model):
    """
    Logical grouping of questions. Used to paginate forms into steps.
    """

    form = models.ForeignKey(
        FormDefinition,
        on_delete=models.CASCADE,
        related_name="sections",
    )

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    position = models.PositiveIntegerField(default=0)
    show_if_question = models.ForeignKey(
        "applications.Question",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sections_conditioned",
        help_text="Optional: only show this section if the question equals the expected value.",
    )
    show_if_value = models.CharField(
        max_length=200,
        blank=True,
        help_text="Match is case-insensitive; works for short_text/choice/boolean. Leave blank for no condition.",
    )
    show_if_question_2 = models.ForeignKey(
        "applications.Question",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sections_conditioned_second",
        help_text="Segunda condición opcional.",
    )
    show_if_value_2 = models.CharField(max_length=200, blank=True)
    LOGIC_AND = "AND"
    LOGIC_OR = "OR"
    LOGIC_CHOICES = [
        (LOGIC_AND, "AND"),
        (LOGIC_OR, "OR"),
    ]
    show_if_logic = models.CharField(
        max_length=3,
        choices=LOGIC_CHOICES,
        default=LOGIC_AND,
        help_text="Cómo combinar las 2 condiciones (si ambas existen).",
    )
    show_if_conditions = models.JSONField(
        default=list,
        blank=True,
        help_text="Lista de condiciones [{'question_id':..., 'value':...}]. Usa lógica AND/OR.",
    )

    class Meta:
        ordering = ["position", "id"]
        unique_together = ("form", "title")

    def __str__(self) -> str:
        return f"{self.form.slug} — {self.title}"


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

    confirm_value = models.BooleanField(
        default=False,
        help_text="If true, render a second confirmation input that must match.",
    )

    show_if_question = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dependent_questions",
        help_text="Optional: only show this question when another question has the expected value.",
    )
    show_if_value = models.CharField(
        max_length=200,
        blank=True,
        help_text="Case-insensitive match. For yes/no, use 'yes' or 'no'. For choices, use the stored choice value.",
    )
    show_if_conditions = models.JSONField(
        default=list,
        blank=True,
        help_text="Lista de condiciones [{'question_id':..., 'value':...}]. Usa lógica OR (se muestra si cualquiera coincide).",
    )

    section = models.ForeignKey(
        Section,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="questions",
    )

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
    second_stage_reminder_due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Automatic follow-up reminder due time for invited A1 applicants.",
    )
    second_stage_reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="When the automatic A1->A2 reminder was sent.",
    )

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
    priority_emails_text = models.TextField(
        blank=True,
        default="",
        help_text="Normalized newline-separated emails to force into Priority status during grading.",
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
class PairingJob(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_DONE, "Done"),
        (STATUS_FAILED, "Failed"),
    ]

    group_number = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    log_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def append_log(self, msg: str):
        self.log_text += msg.rstrip() + "\n"
        self.save(update_fields=["log_text", "updated_at"])


class TaskType(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    is_revision_type = models.BooleanField(
        default=False,
        help_text="Used by the Website Revisions page as its default task type.",
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name", "id"]

    def __str__(self) -> str:
        return self.name


class ParticipantEmailStatus(models.Model):
    email = models.EmailField(unique=True, db_index=True)
    participated = models.BooleanField(default=True)
    contract_signed = models.BooleanField(default=False, db_index=True)
    contract_signed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    contract_signature_request_id = models.CharField(max_length=120, blank=True, default="")
    contract_source = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["email"]
        verbose_name = "Participant Email Status"
        verbose_name_plural = "Participant Email Statuses"

    def __str__(self) -> str:
        state = "yes" if self.participated else "no"
        return f"{self.email} ({state})"


class DropboxSignWebhookEvent(models.Model):
    event_type = models.CharField(max_length=120, db_index=True)
    event_time = models.CharField(max_length=64, blank=True, default="")
    event_hash = models.CharField(max_length=128, blank=True, default="")
    signature_request_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    signer_emails_text = models.TextField(blank=True, default="")
    payload_json = models.JSONField(default=dict, blank=True)
    payload_digest = models.CharField(max_length=64, unique=True, db_index=True)
    hash_verified = models.BooleanField(default=False, db_index=True)
    processed = models.BooleanField(default=False, db_index=True)
    marked_count = models.PositiveIntegerField(default=0)
    process_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Dropbox Sign Webhook Event"
        verbose_name_plural = "Dropbox Sign Webhook Events"

    def __str__(self) -> str:
        return f"{self.event_type or 'event'} · {self.signature_request_id or 'no-request-id'}"


class GroupParticipantList(models.Model):
    group = models.OneToOneField(
        "applications.FormGroup",
        on_delete=models.CASCADE,
        related_name="participant_list",
    )
    mentoras_emails_text = models.TextField(blank=True)
    emprendedoras_emails_text = models.TextField(blank=True)
    mentoras_sheet_rows = models.JSONField(default=list, blank=True)
    emprendedoras_sheet_rows = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-group__number"]
        verbose_name = "Group Participant List"
        verbose_name_plural = "Group Participant Lists"

    def __str__(self) -> str:
        return f"Group {self.group.number} participants"


DEFAULT_TASK_TYPES = [
    {
        "slug": "general",
        "name": "General",
        "position": 10,
        "is_revision_type": False,
    },
    {
        "slug": "follow_up",
        "name": "Follow up",
        "position": 20,
        "is_revision_type": False,
    },
    {
        "slug": "website_revision",
        "name": "Website revision",
        "position": 30,
        "is_revision_type": True,
    },
]


def ensure_default_task_types() -> None:
    for cfg in DEFAULT_TASK_TYPES:
        TaskType.objects.get_or_create(
            slug=cfg["slug"],
            defaults={
                "name": cfg["name"],
                "position": cfg["position"],
                "is_revision_type": cfg["is_revision_type"],
                "is_active": True,
            },
        )


class UserTask(models.Model):
    TYPE_GENERAL = "general"
    TYPE_FOLLOW_UP = "follow_up"
    TYPE_WEBSITE_REVISION = "website_revision"
    LEGACY_TASK_TYPE_LABELS = {
        TYPE_GENERAL: "General",
        TYPE_FOLLOW_UP: "Follow up",
        TYPE_WEBSITE_REVISION: "Website revision",
    }

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_UNAVAILABLE = "unavailable"
    STATUS_REVISION_NEEDED = "revision_needed"
    STATUS_DONE = "done"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_UNAVAILABLE, "Unavailable"),
        (STATUS_REVISION_NEEDED, "Revision Needed"),
        (STATUS_DONE, "Completed"),
    ]
    PRIORITY_LOW = "low"
    PRIORITY_MEDIUM = "medium"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"
    PRIORITY_CHOICES = [
        (PRIORITY_URGENT, "P1"),
        (PRIORITY_HIGH, "P2"),
        (PRIORITY_MEDIUM, "P3"),
        (PRIORITY_LOW, "P4"),
    ]

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_tasks",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type = models.CharField(
        max_length=20,
        default=TYPE_GENERAL,
        db_index=True,
    )
    task_type_ref = models.ForeignKey(
        "applications.TaskType",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
    )
    follow_up_requested = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Mark tasks that need follow-up from the team.",
    )
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_MEDIUM,
        db_index=True,
    )
    hours_estimate = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    impact = models.TextField(
        blank=True,
        help_text="What impact this task has and why it matters.",
    )
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.assigned_to} — {self.title}"

    @property
    def task_type_name(self) -> str:
        if self.task_type_ref_id:
            return self.task_type_ref.name
        return self.LEGACY_TASK_TYPE_LABELS.get(self.task_type, self.task_type or "—")
