PARTICIPANT_STATUS_CHOICES = [
    ("NFA", "No firmaron acta"),
    ("NC", "No completaron capacitacion"),
    ("NCP", "Iniciaron, no continuan - problemas en el programa"),
    ("NCPP", "Iniciaron, no continuan - problemas personales"),
    ("SG", "La dejamos para un siguiente grupo"),
    ("CG", "Se cambia de grupo activo al tiempo"),
    ("CP", "Cambiaron de pareja"),
    ("D/NC", "Dificiles pero continuan"),
    ("E", "Excelente/testimonio"),
    ("G", "Se graduo del programa"),
    ("A", "Participante activa"),
]

PARTICIPANT_STATUS_CODES = [code for code, _label in PARTICIPANT_STATUS_CHOICES]

PARTICIPANT_STATUS_SHEET_OPTIONS = [
    "No Firmo A",
    "No Capacitacion",
    "No Continua P",
    "No Continua PP",
    "Siguiente grupo",
    "Cambio de grupo",
    "Cambio de pareja",
    "DIficil/No contacto",
    "Exelente",
    "Graduada",
    "Activa",
]

PARTICIPANT_STATUS_SHEET_OPTION_CODES = dict(
    zip(PARTICIPANT_STATUS_SHEET_OPTIONS, PARTICIPANT_STATUS_CODES)
)

PARTICIPANT_STATUS_LABELS = dict(PARTICIPANT_STATUS_CHOICES)

_PARTICIPANT_STATUS_ALIASES = {
    **{code.upper(): code for code in PARTICIPANT_STATUS_CODES},
    **{
        label.strip().upper(): code
        for label, code in PARTICIPANT_STATUS_SHEET_OPTION_CODES.items()
    },
}


def normalize_participant_status(value: str | None) -> str:
    status = str(value or "").strip()
    if not status:
        return ""
    return _PARTICIPANT_STATUS_ALIASES.get(status.upper(), status.upper())

PARTICIPANT_STATUS_STARTED = {"NCP", "NCPP", "CG", "CP", "D/NC", "E", "G", "A"}
PARTICIPANT_STATUS_GRADUATED = {"G"}

PARTICIPANT_STATUS_COLORS = {
    "NFA": "#dbeafe",
    "NC": "#fee2e2",
    "NCP": "#fef3c7",
    "NCPP": "#ffedd5",
    "SG": "#e0e7ff",
    "CG": "#dbeafe",
    "CP": "#dcfce7",
    "D/NC": "#e5e7eb",
    "E": "#cffafe",
    "G": "#d1fae5",
    "A": "#ede9fe",
}
