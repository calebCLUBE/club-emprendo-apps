# applications/management/commands/seed_surveys.py
from __future__ import annotations

import re
import unicodedata
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from applications.models import FormDefinition, Question, Choice


def _slugify(s: str, max_len: int = 50) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:max_len] or "q")


def _qtype(field_type: str) -> str:
    # map our “text names” → your model constants
    return {
        "short_text": Question.SHORT_TEXT,
        "long_text": Question.LONG_TEXT,
        "integer": Question.INTEGER,
        "boolean": Question.BOOLEAN,
        "choice": Question.CHOICE,
        "multi_choice": Question.MULTI_CHOICE,
    }[field_type]


# --------------------------
# Survey data (pulled from your DOCX files)
# Slugs are deterministic: "01_<slugified text>"
# --------------------------

SURVEYS: dict[str, dict[str, Any]] = {
    "PRIMER_E": {
        "name": "Survey Primer – Emprendedora",
        "questions": [
            {"position": 1, "slug": "01_resultado_club_emprendo", "text": "Resultado: Club Emprendo", "field_type": "short_text", "required": True, "choices": []},
            {"position": 2, "slug": "02_nombre", "text": "Nombre", "field_type": "short_text", "required": True, "choices": []},
            {"position": 3, "slug": "03_correo_electronico", "text": "Correo electrónico", "field_type": "short_text", "required": True, "choices": []},
            {"position": 4, "slug": "04_nombre_de_tu_emprendimiento", "text": "Nombre de tu emprendimiento", "field_type": "short_text", "required": True, "choices": []},
            {"position": 5, "slug": "05_pais_de_residencia", "text": "País de residencia", "field_type": "short_text", "required": True, "choices": []},
            {"position": 6, "slug": "06_anota_el_nombre_de_tu_mentora", "text": "Anota el nombre de tu mentora", "field_type": "short_text", "required": True, "choices": []},
            {"position": 7, "slug": "07_de_que_manera_te_parece_que_cl", "text": "¿De qué manera te parece que Club Emprendo puede ayudarte? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 8, "slug": "08_que_te_gustaria_lograr_con_es", "text": "¿Qué te gustaría lograr con este programa de mentoría de Club Emprendo? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 9, "slug": "09_motivo_de_participacion_por_fa", "text": "Motivo de participación (por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 10, "slug": "10_en_una_escala_de_1_a_5_que_t", "text": "En una escala de 1 a 5, ¿Qué tan satisfecha estás actualmente con tu emprendimiento? (En el aspecto financiero, la calidad de tu producto, crecimiento, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 11, "slug": "11_que_te_gustaria_cambiar_en_tu", "text": "¿Qué te gustaría cambiar en tu emprendimiento? (Puedes responder con más de una cosa)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 12, "slug": "12_en_una_escala_de_1_a_5_que_t", "text": "En una escala de 1 a 5, ¿Qué tan motivada estás para comprometerte a hacer cambios importantes en tu negocio durante estos próximos tres meses, en caso de que tu mentora lo recomiende?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 13, "slug": "13_en_una_escala_de_1_a_5_que_t", "text": "En una escala de 1 a 5, ¿Qué tan segura estás en tu capacidad de lograr tus metas dentro del programa de mentoría, junto a tu mentora?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 14, "slug": "14_en_una_escala_de_1_a_5_que_t", "text": "En una escala de 1 a 5, ¿Qué tanta claridad tienes sobre tus metas de crecimiento como emprendedora?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 15, "slug": "15_en_una_escala_de_1_a_5_que_t", "text": "En una escala de 1 a 5, ¿Qué tan apoyada te sientes en tu día a día para poder crecer tu emprendimiento? (Por tu familia, amigos, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 16, "slug": "16_en_una_escala_de_1_a_5_que_t", "text": "En una escala de 1 a 5, ¿Qué tanta confianza tienes en ti misma como emprendedora? (En cuanto a tu capacidad para manejar retos, tomar decisiones, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 17, "slug": "17_tienes_algun_miedo_o_preocupac", "text": "¿Tienes algún miedo o preocupación antes de iniciar este proceso de mentoría? Por favor explica brevemente.", "field_type": "short_text", "required": True, "choices": []},
            {"position": 18, "slug": "18_comentarios_adicionales", "text": "Comentarios adicionales", "field_type": "short_text", "required": False, "choices": []},
        ],
    },

    "PRIMER_M": {
        "name": "Survey Primer – Mentora",
        "questions": [
            {"position": 1, "slug": "01_resultado_club_emprendo", "text": "Resultado: Club Emprendo", "field_type": "short_text", "required": True, "choices": []},
            {"position": 2, "slug": "02_nombre", "text": "Nombre", "field_type": "short_text", "required": True, "choices": []},
            {"position": 3, "slug": "03_correo_electronico", "text": "Correo electrónico", "field_type": "short_text", "required": True, "choices": []},
            {"position": 4, "slug": "04_pais_de_residencia", "text": "País de residencia", "field_type": "short_text", "required": True, "choices": []},
            {"position": 5, "slug": "05_anota_el_nombre_de_tu_emprend", "text": "Anota el nombre de tu emprendedora", "field_type": "short_text", "required": True, "choices": []},
            {"position": 6, "slug": "06_que_te_motiva_a_ser_mentora", "text": "¿Qué te motiva a ser mentora?", "field_type": "short_text", "required": True, "choices": []},
            {"position": 7, "slug": "07_que_te_gustaria_lograr_en_esta", "text": "¿Qué te gustaría lograr en esta experiencia de mentoría? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 8, "slug": "08_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan capaz te sientes para apoyar a tu emprendedora en el logro de sus metas?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 9, "slug": "09_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan motivada estás para comprometerte a hacer un buen trabajo como mentora durante estos próximos tres meses?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 10, "slug": "10_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan segura te sientes de tu capacidad para generar un espacio seguro de confianza para tu emprendedora?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 11, "slug": "11_comentarios_adicionales", "text": "Comentarios adicionales", "field_type": "short_text", "required": False, "choices": []},
        ],
    },

    "FINAL_E": {
        "name": "Survey Final – Emprendedora",
        "questions": [
            {"position": 1, "slug": "01_resultado_club_emprendo", "text": "Resultado: Club Emprendo", "field_type": "short_text", "required": True, "choices": []},
            {"position": 2, "slug": "02_nombre", "text": "Nombre", "field_type": "short_text", "required": True, "choices": []},
            {"position": 3, "slug": "03_correo_electronico", "text": "Correo electrónico", "field_type": "short_text", "required": True, "choices": []},
            {"position": 4, "slug": "04_nombre_de_tu_emprendimiento", "text": "Nombre de tu emprendimiento", "field_type": "short_text", "required": True, "choices": []},
            {"position": 5, "slug": "05_pais_de_residencia", "text": "País de residencia", "field_type": "short_text", "required": True, "choices": []},
            {"position": 6, "slug": "06_anota_el_nombre_de_tu_mentora", "text": "Anota el nombre de tu mentora", "field_type": "short_text", "required": True, "choices": []},
            {"position": 7, "slug": "07_durante_el_programa_de_mentori", "text": "Durante el programa de mentoría, ¿te mantuviste comprometida (asistiendo a reuniones y haciendo tareas) con el programa? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 8, "slug": "08_si_hubieron_razones_para_no_p", "text": "Si hubieron razones para no poder cumplir con el compromiso del programa, ¿cuáles fueron? (por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 9, "slug": "09_como_calificarias_tu_experien", "text": "¿Cómo calificarías tu experiencia general en el programa de Club Emprendo? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 10, "slug": "10_que_fue_lo_mas_valioso_del_pr", "text": "¿Qué fue lo más valioso del programa de Club Emprendo para ti? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 11, "slug": "11_por_que_escogiste_esa_opcion", "text": "¿Por qué escogiste esa opción? (Por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 12, "slug": "12_que_fue_lo_menos_valioso_del", "text": "¿Qué fue lo menos valioso del programa de Club Emprendo para ti? (Por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 13, "slug": "13_hubieron_aspectos_del_program", "text": "¿Hubieron aspectos del programa que no te gustaron? ¿Cuáles?", "field_type": "short_text", "required": True, "choices": []},
            {"position": 14, "slug": "14_tu_mentora_fue_puntual_para_l", "text": "Tu mentora fue puntual para las reuniones y el proceso de mentoría", "field_type": "short_text", "required": True, "choices": []},
            {"position": 15, "slug": "15_tu_mentora_te_brindo_consejos", "text": "Tu mentora te brindó consejos útiles para tu emprendimiento", "field_type": "short_text", "required": True, "choices": []},
            {"position": 16, "slug": "16_tu_mentora_escuchaba_tus_inqu", "text": "Tu mentora escuchaba tus inquietudes y te brindaba sugerencias", "field_type": "short_text", "required": True, "choices": []},
            {"position": 17, "slug": "17_tu_mentora_te_ayudo_a_reconoc", "text": "Tu mentora te ayudó a reconocer tus fortalezas personales y a confiar en ti misma", "field_type": "short_text", "required": True, "choices": []},
            {"position": 18, "slug": "18_tu_mentora_te_ayudo_a_estable", "text": "Tu mentora te ayudó a establecer y trabajar hacia tus metas como emprendedora", "field_type": "short_text", "required": True, "choices": []},
            {"position": 19, "slug": "19_te_sentiste_escuchada_y_compre", "text": "¿Te sentiste escuchada y comprendida por tu mentora?", "field_type": "short_text", "required": True, "choices": []},
            {"position": 20, "slug": "20_cual_fue_el_mejor_aspecto_de", "text": "¿Cuál fue el mejor aspecto de tu mentora?", "field_type": "short_text", "required": True, "choices": []},
            {"position": 21, "slug": "21_que_cambios_notaste_en_ti_mis", "text": "¿Qué cambios notaste en ti misma como emprendedora durante el programa de Club Emprendo? (por ejemplo, en tu motivación, confianza, metas, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 22, "slug": "22_que_cambios_notaste_en_tu_ne", "text": "¿Qué cambios notaste en tu negocio durante el programa de Club Emprendo? (por ejemplo, más ventas, mejora del producto, redes sociales, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 23, "slug": "23_que_podria_haberse_hecho_dife", "text": "¿Qué podría haberse hecho diferente para mejorar tu experiencia? (por ejemplo, la mentoría, talleres, tareas, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 24, "slug": "24_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan satisfecha estás actualmente con tu emprendimiento? (En el aspecto financiero, la calidad de tu producto, crecimiento, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 25, "slug": "25_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan motivada estás para seguir implementando cambios importantes en tu negocio, en caso de que tu mentora lo recomiende?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 26, "slug": "26_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan segura estás en tu capacidad de seguir logrando tus metas como emprendedora?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 27, "slug": "27_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tanta claridad tienes sobre tus metas de crecimiento como emprendedora?", "field_type": "long_text", "required": True, "choices": []},
            {"position": 28, "slug": "28_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tan apoyada te sientes en tu día a día para poder crecer tu emprendimiento? (Por tu familia, amigos, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 29, "slug": "29_en_una_escala_del_1_al_5_que_t", "text": "En una escala del 1 al 5, ¿Qué tanta confianza tienes en ti misma como emprendedora? (En cuanto a tu capacidad para manejar retos, tomar decisiones, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 30, "slug": "30_recomendarias_el_programa_de_c", "text": "¿Recomendarías el programa de Club Emprendo a una amiga emprendedora? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 31, "slug": "31_te_interesaria_participar_en_f", "text": "¿Te interesaría participar en futuros programas o actividades de Club Emprendo? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 32, "slug": "32_comentarios_adicionales", "text": "Comentarios adicionales", "field_type": "short_text", "required": False, "choices": []},
        ],
    },

    "FINAL_M": {
        "name": "Survey Final – Mentora",
        "questions": [
            {"position": 1, "slug": "01_resultado_club_emprendo", "text": "Resultado: Club Emprendo", "field_type": "short_text", "required": True, "choices": []},
            {"position": 2, "slug": "02_nombre", "text": "Nombre", "field_type": "short_text", "required": True, "choices": []},
            {"position": 3, "slug": "03_correo_electronico", "text": "Correo electrónico", "field_type": "short_text", "required": True, "choices": []},
            {"position": 4, "slug": "04_pais_de_residencia", "text": "País de residencia", "field_type": "short_text", "required": True, "choices": []},
            {"position": 5, "slug": "05_anota_el_nombre_de_tu_emprend", "text": "Anota el nombre de tu emprendedora", "field_type": "short_text", "required": True, "choices": []},
            {"position": 6, "slug": "06_te_mantuviste_comprometida_as", "text": "¿Te mantuviste comprometida (asistiendo a reuniones y apoyando a tu emprendedora) durante el programa? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 7, "slug": "07_si_hubieron_razones_para_no_p", "text": "Si hubieron razones para no poder cumplir con el compromiso del programa, ¿cuáles fueron? (por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 8, "slug": "08_como_calificarias_tu_experien", "text": "¿Cómo calificarías tu experiencia general como mentora en el programa de Club Emprendo? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 9, "slug": "09_que_fue_lo_mas_valioso_del_pr", "text": "¿Qué fue lo más valioso del programa de Club Emprendo para ti como mentora? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 10, "slug": "10_por_que_escogiste_esa_opcion", "text": "¿Por qué escogiste esa opción? (por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 11, "slug": "11_que_fue_lo_menos_valioso_del", "text": "¿Qué fue lo menos valioso del programa de Club Emprendo para ti? (por favor explica brevemente)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 12, "slug": "12_hubieron_aspectos_del_program", "text": "¿Hubieron aspectos del programa que no te gustaron? ¿Cuáles?", "field_type": "short_text", "required": True, "choices": []},
            {"position": 13, "slug": "13_que_aprendiste_de_tu_emprend", "text": "¿Qué aprendiste de tu emprendedora durante el proceso?", "field_type": "short_text", "required": True, "choices": []},
            {"position": 14, "slug": "14_que_podria_haberse_hecho_dife", "text": "¿Qué podría haberse hecho diferente para mejorar tu experiencia? (por ejemplo, la mentoría, talleres, tareas, etc.)", "field_type": "long_text", "required": True, "choices": []},
            {"position": 15, "slug": "15_recomendarias_ser_mentora_en_c", "text": "¿Recomendarías ser mentora en Club Emprendo a una amiga? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 16, "slug": "16_te_interesaria_participar_en_f", "text": "¿Te interesaría participar en futuros programas o actividades de Club Emprendo? (Marcar solo una opción)", "field_type": "short_text", "required": True, "choices": []},
            {"position": 17, "slug": "17_comentarios_adicionales", "text": "Comentarios adicionales", "field_type": "short_text", "required": False, "choices": []},
        ],
    },
}


class Command(BaseCommand):
    help = "Seed the 4 surveys (Primer/Final for Emprendedora/Mentora) into FormDefinition/Question/Choice."

    def add_arguments(self, parser):
        parser.add_argument(
            "--wipe",
            action="store_true",
            help="Delete existing survey definitions (slugs PRIMER_E, PRIMER_M, FINAL_E, FINAL_M) before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        wipe = bool(options.get("wipe"))

        slugs = list(SURVEYS.keys())
        if wipe:
            self.stdout.write(self.style.WARNING("Wiping existing survey FormDefinitions..."))
            FormDefinition.objects.filter(slug__in=slugs).delete()

        for slug, payload in SURVEYS.items():
            fd, created = FormDefinition.objects.get_or_create(
                slug=slug,
                defaults={"name": payload["name"]},
            )
            if not created and fd.name != payload["name"]:
                fd.name = payload["name"]
                fd.save(update_fields=["name"])

            # Upsert questions by slug within this form
            for qd in payload["questions"]:
                q_slug = qd["slug"]
                q, q_created = Question.objects.get_or_create(
                    form=fd,
                    slug=q_slug,
                    defaults={
                        "text": qd["text"],
                        "field_type": _qtype(qd["field_type"]),
                        "required": bool(qd["required"]),
                        "active": True,
                        "position": int(qd["position"]),
                        "help_text": "",
                    },
                )

                # Update if already exists
                changed = False
                if q.text != qd["text"]:
                    q.text = qd["text"]; changed = True
                new_ft = _qtype(qd["field_type"])
                if q.field_type != new_ft:
                    q.field_type = new_ft; changed = True
                if q.required != bool(qd["required"]):
                    q.required = bool(qd["required"]); changed = True
                if q.position != int(qd["position"]):
                    q.position = int(qd["position"]); changed = True
                if not q.active:
                    q.active = True; changed = True
                if changed:
                    q.save()

                # Replace choices if this question has them
                Choice.objects.filter(question=q).delete()
                for idx, cd in enumerate(qd["choices"], start=1):
                    Choice.objects.create(
                        question=q,
                        label=cd["label"],
                        value=cd["value"],
                        position=idx,
                    )

            self.stdout.write(self.style.SUCCESS(f"Seeded {fd.slug}: {fd.name}"))

        self.stdout.write(self.style.SUCCESS("All surveys seeded."))
