import re
import unicodedata


# These are the only choices offered for new status edits.
PARTICIPANT_STATUS_CHOICES = [
    ("NFA", "No firmo acta"),
    ("NC", "No Capacitacion"),
    ("A", "Activa"),
    ("G", "Graduada"),
    ("NCPP", "NC - Personal"),
    ("NCP", "NC - Programa"),
    ("NCSR", "NC - Sin Respuesta"),
    ("SG", "Siguiente Grupo"),
    ("CG", "Cambio de Grupo"),
]
PARTICIPANT_STATUS_CODES = [code for code, _label in PARTICIPANT_STATUS_CHOICES]
PARTICIPANT_STATUS_SHEET_OPTIONS = [label for _code, label in PARTICIPANT_STATUS_CHOICES]
PARTICIPANT_STATUS_SHEET_OPTION_CODES = {
    label: code for code, label in PARTICIPANT_STATUS_CHOICES
}
PARTICIPANT_STATUS_LABELS = dict(PARTICIPANT_STATUS_CHOICES)
# Keep retired statuses readable in historical records without offering them as choices.
PARTICIPANT_STATUS_SHEET_LABELS = {
    "CP": "Cambio de pareja", "D/NC": "DIficil/No contacto", "E": "Exelente",
    **PARTICIPANT_STATUS_LABELS,
}


def _status_alias_key(value: str) -> str:
    unaccented = unicodedata.normalize("NFKD", value.casefold())
    return re.sub(r"[^a-z0-9]", "", unaccented)


_LEGACY_STATUS_ALIASES = {
    "No Firmo A": "NFA", "No firmaron acta": "NFA",
    "No completaron capacitacion": "NC",
    "No Continua P": "NCP", "No Continua PP": "NCPP",
    "Iniciaron, no continuan - problemas en el programa": "NCP",
    "Iniciaron, no continuan - problemas personales": "NCPP",
    "La dejamos para un siguiente grupo": "SG",
    "Se cambia de grupo activo al tiempo": "CG",
    "Cambiaron de pareja": "CP", "Dificiles pero continuan": "D/NC",
    "Excelente/testimonio": "E", "Se graduo del programa": "G",
    "Participante activa": "A",
    **{label: code for code, label in PARTICIPANT_STATUS_SHEET_LABELS.items()},
}
_PARTICIPANT_STATUS_ALIASES = {
    _status_alias_key(label): code
    for label, code in {
        **_LEGACY_STATUS_ALIASES,
        **PARTICIPANT_STATUS_SHEET_OPTION_CODES,
        **{code: code for code in PARTICIPANT_STATUS_SHEET_LABELS},
    }.items()
}


def normalize_participant_status(value: str | None) -> str:
    status = str(value or "").strip()
    if not status:
        return ""
    return _PARTICIPANT_STATUS_ALIASES.get(_status_alias_key(status), status.upper())


PARTICIPANT_STATUS_NOT_STARTED = {"NFA", "NC"}
PARTICIPANT_STATUS_STARTED = {"A", "G", "NCP", "NCPP", "NCSR"}
PARTICIPANT_STATUS_DROPPED_OUT = {"NCP", "NCPP", "NCSR"}
PARTICIPANT_STATUS_TRANSFERRED = {"SG", "CG"}
PARTICIPANT_STATUS_GRADUATED = {"G"}
PARTICIPANT_STATUS_GRADUATION_ELIGIBLE = (
    PARTICIPANT_STATUS_GRADUATED | PARTICIPANT_STATUS_DROPPED_OUT
)
PARTICIPANT_STATUS_COLORS = {
    "NFA": "#dbeafe", "NC": "#fee2e2", "NCP": "#fef3c7", "NCPP": "#ffedd5",
    "NCSR": "#fecaca", "SG": "#e0e7ff", "CG": "#dbeafe", "CP": "#dcfce7",
    "D/NC": "#e5e7eb", "E": "#cffafe", "G": "#d1fae5", "A": "#ede9fe",
}
