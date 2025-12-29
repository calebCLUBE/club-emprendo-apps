# applications/master_form_specs.py

MASTER_FORMS = {
    "E_A1": {
        "name": "E.A1 (Emprendedora) - Grupo base",
        "skip_labels": {"Correo electrónico", "Nombre completo"},
        "questions": [
            {"label": "Pais", "type": "short_text", "required": True},
            {"label": "Ciudad", "type": "short_text", "required": True},
            {"label": "Nombre del emprendimiento", "type": "short_text", "required": True},
            {"label": "Industria/sector", "type": "short_text", "required": True},
            {"label": "¿Tu emprendimiento ya está en funcionamiento?", "type": "single_choice", "required": True,
             "options": ["Sí, ya estamos vendiendo/operando", "No, todavía estamos en etapa de idea/preparación"]},
            {"label": "Redes sociales del emprendimiento", "type": "short_text", "required": False},
            {"label": "Cuéntanos brevemente qué haces y qué vendes (o planeas vender)", "type": "long_text", "required": True},
        ],
    },

    "E_A2": {
        "name": "E.A2 (Emprendedora) - Grupo base",
        "skip_labels": {"Correo electrónico", "Nombre completo"},
        "questions": [
            # NOTE: This file is longer; keep adding items exactly as your PDF shows them.
            # I’m including the common structure; paste the remaining questions following this pattern.
            {"label": "Pais", "type": "short_text", "required": True},
            {"label": "Ciudad", "type": "short_text", "required": True},

            # Add every question from G5-E.A2 PDF here...
        ],
    },

    "M_A1": {
        "name": "M.A1 (Mentora) - Grupo base",
        "skip_labels": {"Correo electrónico", "Nombre completo"},
        "questions": [
            {"label": "Pais", "type": "short_text", "required": True},
            {"label": "Ciudad", "type": "short_text", "required": True},
            {"label": "Área principal de experiencia", "type": "short_text", "required": True},
            {"label": "Años de experiencia", "type": "single_choice", "required": True,
             "options": ["0-2", "3-5", "6-10", "10+"]},
            {"label": "LinkedIn (opcional)", "type": "short_text", "required": False},
            {"label": "Cuéntanos en qué temas te gustaría mentorizar", "type": "long_text", "required": True},
        ],
    },

    "M_A2": {
        "name": "M.A2 (Mentora) - Grupo base",
        "skip_labels": {"Correo electrónico", "Nombre completo"},
        "questions": [
            # Add every question from G5-M.A2 PDF here...
            {"label": "Pais", "type": "short_text", "required": True},
            {"label": "Ciudad", "type": "short_text", "required": True},
        ],
    },
}
