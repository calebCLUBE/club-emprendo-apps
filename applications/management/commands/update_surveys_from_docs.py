# applications/management/commands/update_surveys_from_docs.py
from __future__ import annotations

from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from applications.models import FormDefinition, Question, Choice


def _make_slug(i: int) -> str:
    # Guaranteed unique within a form, stable across reruns
    return f"s{i:02d}"


def _add_question(
    form_def: FormDefinition,
    position: int,
    text: str,
    field_type: str,
    required: bool = True,
    help_text: str = "",
    choices: list[tuple[str, str]] | None = None,
) -> Question:
    q = Question.objects.create(
        form=form_def,
        text=text,
        help_text=help_text or "",
        field_type=field_type,
        required=required,
        position=position,
        slug=_make_slug(position),
        active=True,
    )
    if choices:
        for idx, (label, value) in enumerate(choices, start=1):
            Choice.objects.create(
                question=q,
                label=label,
                value=value,
                position=idx,
            )
    return q


def _scale_choices(min_v: int, max_v: int) -> list[tuple[str, str]]:
    return [(str(v), str(v)) for v in range(min_v, max_v + 1)]


def _choice_pairs(options: Iterable[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for o in options:
        label = (o or "").strip()
        if not label:
            continue
        value = slugify(label) or label
        out.append((label, value))
    return out


class Command(BaseCommand):
    help = "Replace survey questions/options for specified FormDefinition slugs."

    def add_arguments(self, parser):
        parser.add_argument("--m_primer", required=True, help="FormDefinition.slug for Mentoras 'Primer' survey")
        parser.add_argument("--m_final", required=True, help="FormDefinition.slug for Mentoras 'Final' survey")
        parser.add_argument("--e_primer", required=True, help="FormDefinition.slug for Emprendedoras 'Primer' survey")
        parser.add_argument("--e_final", required=True, help="FormDefinition.slug for Emprendedoras 'Final' survey")
        parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print plan, but don't write to DB")

    def handle(self, *args, **opts):
        slugs = {
            "M_PRIMER": opts["m_primer"],
            "M_FINAL": opts["m_final"],
            "E_PRIMER": opts["e_primer"],
            "E_FINAL": opts["e_final"],
        }

        forms: dict[str, FormDefinition] = {}
        for key, slug in slugs.items():
            fd = FormDefinition.objects.filter(slug=slug).first()
            if not fd:
                raise CommandError(f"FormDefinition not found for {key}: {slug}")
            forms[key] = fd

        # Must match your Question.field_type choices:
        # short_text, long_text, integer, boolean, choice, multi_choice
        FT_LONG = "long_text"
        FT_SINGLE = "choice"

        # ----------------------------
        # Survey definitions (from your DOCX tables)
        # Rule: if middle column has options => single choice
        # If blank => long text
        # Scales => single choice with numeric options
        # ----------------------------

        # PRIMER_M (Primer - M.docx)
        # :contentReference[oaicite:4]{index=4}
        M_PRIMER = [
            ("Nombre completo *", None, ""),
            ("Correo electrónico *", None, ""),
            ("¿Completaste la capacitación que se te ofreció? *", ["Sí", "No", "Parcialmente"], ""),
            ("¿La duración de la capacitación fue adecuada para aprender los contenidos? *",
             ["Adecuada", "Poco adecuada — fue demasiado largo", "Poco adecuada — fue demasiado corto"], ""),
            ("¿Cómo calificarías tu preparación para la mentoría antes de completar la capacitación para mentores? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Cómo calificarías tu preparación para la mentoría después de completar la capacitación para mentores? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("En general, ¿cómo calificarías tu experiencia con el contenido proporcionado en la capacitación para mentoras? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Qué tan bien entiendes el marco del Plan de Vida SetPath? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Qué tan preparada te sientes para liderar las conversaciones de mentoría? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Qué recomendarías cambiar (si es que hay algo) en la capacitación de mentores?", None, ""),
            ("¿Algún comentario adicional que tengas?", None, ""),
        ]

        # FINAL_M (Final - M.docx)
        # :contentReference[oaicite:5]{index=5}
        M_FINAL = [
            ("Nombre completo *", None, ""),
            ("Correo Electronico *", None, ""),
            ("Nombre de tu emprendedora *", None, ""),
            ("Satisfacción con la experiencia de mentoría *", ("scale", 1, 5), "Escala 1 → 5"),
            ("Tiempo promedio por semana que pasas en reuniones con tu emprendedora estudiante *",
             ["No se reunieron", "menos de 1 hora", "entre 1 hora y 2 horas", "mas que 2 horas"], ""),
            ("Tiempo promedio por semana dedicado a tu emprendedora fuera de las reuniones semanales *",
             ["No se reunieron", "menos de 1 hora", "entre 1 hora y 2 horas", "mas que 2 horas"], ""),
            ("¿Consideras que el tiempo invertido fue el adecuado? *",
             ["Muy poco tiempo", "El tiempo fue adecuado", "Demasiado tiempo"], ""),
            ("La emprendedora estuvo comprometida y participó activamente en las reuniones *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Dónde crees que tu emprendedora estudiante necesita más apoyo? *",
             ["Acceso a financiamiento", "Gestión de inventarios", "Gestión financiera / contabilidad",
              "Liderazgo y toma de decisiones", "Marketing", "Modelo de negocio / estrategia",
              "Motivación, ánimo, autoconfianza", "Networking / redes de contacto",
              "Ventas y adquisición de clientes", "other"], ""),
            ("¿Qué tipo de apoyo adicional necesitas para mejorar tu labor de mentora? *", None, ""),
            ("¿Volverías a ser mentora en el futuro? *", ["Sí", "No"], ""),
            ("¿Cómo calificarías la calidad del apoyo y los recursos proporcionados a las mentoras? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Tienes algún comentario sobre tu método de comunicación preferido?", None, ""),
            ("Por favor, describe una acción más valiosa que has realizado en tu vida personal y/o negocio, gracias a tu experiencia de mentoría con Club Emprendo *",
             None, ""),
            ("¿Tienes sugerencias específicas para mejorar el programa?", None, ""),
            ("¿Algún comentario adicional que tengas?", None, ""),
        ]

        # PRIMER_E (Primer - E.docx)
        # :contentReference[oaicite:6]{index=6}
        E_PRIMER = [
            ("Nombre completo *", None, ""),
            ("Correo electrónico *", None, ""),
            ("Nombre del negocio *", None, ""),
            ("Industria del negocio *",
             ["Agricultura", "Alimentos y bebidas", "Artesanías", "Belleza y cuidado personal",
              "Comercio minorista", "Construcción y remodelación", "Educación y capacitación",
              "Finanzas y servicios legales", "Inmobiliaria", "Medios y comunicaciones",
              "Salud y bienestar", "Servicios (ej. limpieza, cuidado de niños, turismo)",
              "Tecnología", "Textiles y ropa", "Transporte y logística", "Otro"], ""),
            ("In what city/country is your business located?", None, ""),
            ("En tu emprendimiento tuviste algún empleado el mes pasado *",
             ["Sí", "No"], "Nota: en el doc aparece lógica (Sí → 9; No → 11). Actualmente el sitio no oculta preguntas automáticamente."),
            ("Número total de empleados a medio tiempo del mes pasado", None, ""),
            ("Número total de empleados a tiempo completo del mes pasado", None, ""),
            ("¿Te pagaste un salario a ti mismo a través de tu negocio el mes pasado *",
             ["Sí", "No", "No entiendo la pregunta"], ""),
            ("¿Tienes un sistema de contabilidad establecido para llevar el control de las finanzas de tu negocio *",
             ["Sí", "No"], ""),
            ("¿Mantienes separado el dinero de tu negocio y el de tu hogar *",
             ["Sí", "No"], ""),
            ("¿Te sientes satisfecha con tu vida en general *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Tienes confianza en tu capacidad para tomar decisiones que impacten positivamente tu futuro *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Tienes confianza en tu capacidad para satisfacer tus necesidades básicas sin asistencia externa *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Qué tanta confianza tienes en este momento en la gestión de tu emprendimiento *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Cuánta claridad tienes sobre los siguientes pasos a tomar para el crecimiento del negocio *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("Después de la capacitación de lanzamiento, ¿cómo te sentiste con respecto a tu preparación para comenzar el programa de mentoría *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Algún comentario adicional que tengas?", None, ""),
        ]

        # FINAL_E (Final - E.docx)
        # :contentReference[oaicite:7]{index=7}
        E_FINAL = [
            ("Nombre completo *", None, ""),
            ("Email *", None, ""),
            ("¿En tu emprendimiento tuviste algún empleado el mes pasado? *",
             ["Sí", "No"], "Nota: en el doc aparece lógica (Sí → 9; No → 11). Actualmente el sitio no oculta preguntas automáticamente."),
            ("Número total de empleados a medio tiempo del mes pasado", None, ""),
            ("Número total de empleados a tiempo completo del mes pasado", None, ""),
            ("¿Te pagaste un salario a ti mismo a través de tu negocio el mes pasado? *",
             ["Sí", "No", "No entiendo la pregunta"], ""),
            ("¿Tienes un sistema de contabilidad establecido para llevar el control de las finanzas de tu negocio? *",
             ["Sí", "No"], ""),
            ("¿Mantienes separado el dinero de tu negocio y el de tu hogar? *",
             ["Sí", "No"], ""),
            ("¿Te sientes satisfecha con tu vida en general? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Tienes confianza en tu capacidad para tomar decisiones que impacten positivamente tu futuro? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Tienes confianza en tu capacidad para satisfacer tus necesidades básicas sin asistencia externa? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Qué tanta confianza tienes en este momento en la gestión de tu emprendimiento? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("¿Cuánta claridad tienes sobre los siguientes pasos a tomar para el crecimiento del negocio? *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("Nombre de tu mentora *", None, ""),
            ("¿Te has reunido con tu mentora todas las semanas hasta ahora? *",
             ["Sí, nos hemos reunido todas las semanas", "Nos hemos perdido 1 semana",
              "Nos hemos perdido 2 semanas", "Nos hemos perdido 3 semanas o más"], ""),
            ("Tiempo promedio por semana que pasas en reuniones con tu mentora *",
             ["No se reunieron", "menos de 1 hora", "Entre 1 hora y 2 horas", "mas que 2 horas"], ""),
            ("Tiempo promedio por semana dedicado a la mentoría fuera de las reuniones semanales *",
             ["No se reunieron", "menos de 1 hora", "Entre 1 hora y 2 horas", "mas que 2 horas"], ""),
            ("Satisfacción con la experiencia de mentoría *",
             ("scale", 1, 5), "Escala 1 → 5"),
            ("How likely are you to recommend this mentorship program to a friend?",
             ("scale", 0, 10), "Escala 0 → 10"),
            ("¿Consideras que el tiempo invertido fue el adecuado? *",
             ["Muy poco tiempo", "El tiempo fue adecuado", "Demasiado tiempo"], ""),
            ("¿Dónde crees que necesitas más apoyo? *",
             ["Acceso a financiamiento", "Gestión de inventarios", "Gestión financiera / contabilidad",
              "Liderazgo y toma de decisiones", "Marketing", "Modelo de negocio / estrategia",
              "Motivación, ánimo, autoconfianza", "Networking / redes de contacto",
              "Ventas y adquisición de clientes", "Other"], ""),
            ("Por favor, describe la acción más valiosa que has realizado en tu vida personal y/o negocio, gracias a tu experiencia de mentoría con Club Emprendo *",
             None, ""),
            ("¿Algún comentario adicional que tengas? *", None, ""),
        ]

        plan = {
            forms["M_PRIMER"]: M_PRIMER,
            forms["M_FINAL"]: M_FINAL,
            forms["E_PRIMER"]: E_PRIMER,
            forms["E_FINAL"]: E_FINAL,
        }

        # Print plan
        self.stdout.write("Survey update plan:")
        for fd, items in plan.items():
            structured = sum(1 for _, opt, _ in items if opt is not None)
            self.stdout.write(f" - {fd.slug}: {len(items)} questions ({structured} structured)")

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run: no DB changes made."))
            return

        with transaction.atomic():
            for fd, items in plan.items():
                # wipe existing questions for that survey form
                Question.objects.filter(form=fd).delete()

                for idx, (text, opt, help_text) in enumerate(items, start=1):
                    required = "*" in text
                    clean_text = text.replace("*", "").strip()

                    if opt is None:
                        _add_question(
                            fd,
                            idx,
                            clean_text,
                            field_type=FT_LONG,
                            required=required,
                            help_text=help_text,
                        )
                        continue

                    # Scales -> single choice with numeric options
                    if isinstance(opt, tuple) and opt[0] == "scale":
                        _add_question(
                            fd,
                            idx,
                            clean_text,
                            field_type=FT_SINGLE,
                            required=required,
                            help_text=help_text,
                            choices=_scale_choices(opt[1], opt[2]),
                        )
                        continue

                    # Options list -> single choice
                    _add_question(
                        fd,
                        idx,
                        clean_text,
                        field_type=FT_SINGLE,
                        required=required,
                        help_text=help_text,
                        choices=_choice_pairs(opt),
                    )

        self.stdout.write(self.style.SUCCESS("Surveys updated successfully."))
