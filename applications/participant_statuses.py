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

PARTICIPANT_STATUS_LABELS = dict(PARTICIPANT_STATUS_CHOICES)

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
