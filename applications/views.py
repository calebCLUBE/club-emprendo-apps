# applications/views.py
import re
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
import json

from .forms import build_application_form
from .models import Application, Answer, FormDefinition, Question, Section, scheduled_group_open_state
from .drive_sync import schedule_group_track_responses_sync
from .email_templates import build_form_email_context, render_email_template, resolve_form_email_template
from .emprendedora_a1_autograde import (
    autograde_and_email_emprendedora_a1,
    emprendedora_a1_passes,
)
from .grader_e import _disqualification_reasons as _e_a2_disqualification_reasons
from .grader_m import _disqualification_reasons as _m_a2_disqualification_reasons


GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")
THANKS_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
logger = logging.getLogger(__name__)


# -------------------------
# Utilities
# -------------------------
def _latest_group_form_slug(
    suffix: str,
    combined_only: bool | None = None,
    public_only: bool = False,
) -> str | None:
    best = None
    best_num = -1

    for fd in FormDefinition.objects.filter(is_master=False, slug__endswith=suffix).select_related("group"):
        group_num = _group_num_from_form_def(fd)
        if not group_num:
            continue
        if public_only and not bool(getattr(fd, "is_public", False)):
            continue
        if combined_only is not None:
            use_combined = bool(getattr(getattr(fd, "group", None), "use_combined_application", False))
            if combined_only and not use_combined:
                continue
            if not combined_only and use_combined:
                continue
        n = int(group_num)
        if n > best_num:
            best_num = n
            best = fd.slug

    return best


def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def _is_a1_form_slug(slug: str) -> bool:
    return (slug or "").endswith("E_A1") or (slug or "").endswith("M_A1")


def _group_num_from_form_def(form_def: FormDefinition | None) -> str:
    if not form_def:
        return ""
    group = getattr(form_def, "group", None)
    if group and getattr(group, "number", None):
        return str(group.number)
    return _group_num_from_slug(getattr(form_def, "slug", "") or "")


def _group_form_slug_for_master(group, master_slug: str) -> str | None:
    if not group:
        return None

    target_master = (master_slug or "").strip().upper()
    if target_master not in {"E_A1", "E_A2", "M_A1", "M_A2"}:
        return None

    exact = (
        FormDefinition.objects.filter(group=group, is_master=False, slug__endswith=target_master)
        .order_by("id")
        .values_list("slug", flat=True)
        .first()
    )
    if exact:
        return str(exact)
    return None


def _schedule_a1_to_a2_reminder(app: Application):
    slug = app.form.slug or ""
    if not _is_a1_form_slug(slug):
        return

    update_fields: list[str] = []
    # A1 non-rejection emails are disabled, so clear any pending reminder state.
    if app.second_stage_reminder_due_at is not None:
        app.second_stage_reminder_due_at = None
        update_fields.append("second_stage_reminder_due_at")
    if app.second_stage_reminder_sent_at is not None:
        app.second_stage_reminder_sent_at = None
        update_fields.append("second_stage_reminder_sent_at")

    if update_fields:
        app.save(update_fields=update_fields)


def _run_due_a1_to_a2_reminders():
    return


def _maybe_run_due_a1_to_a2_reminders():
    return


def _is_e_a2_na_candidate(answer_map: dict[str, str]) -> bool:
    return bool(_e_a2_disqualification_reasons(answer_map))


def _is_m_a2_na_candidate(answer_map: dict[str, str]) -> bool:
    return bool(_m_a2_disqualification_reasons(answer_map))


def _is_a2_na_candidate(form_slug: str, answer_map: dict[str, str]) -> bool:
    slug = form_slug or ""
    if slug.endswith("E_A2"):
        return _is_e_a2_na_candidate(answer_map)
    if slug.endswith("M_A2"):
        return _is_m_a2_na_candidate(answer_map)
    return False


def _send_a2_submission_email(app: Application, answer_map: dict[str, str]):
    slug = app.form.slug or ""
    if not (slug.endswith("E_A2") or slug.endswith("M_A2")):
        return False

    to_email = (app.email or "").strip()
    if not to_email:
        return False

    send_disqualified_email = _is_a2_na_candidate(slug, answer_map)
    role_word = "emprendedora" if slug.endswith("E_A2") else "mentora"
    replacements = build_form_email_context(
        form_def=app.form,
        role_word=role_word,
        deadline=getattr(getattr(app.form, "group", None), "a2_deadline", None),
    )

    default_subject_na = "Sobre tu aplicación al Programa de Mentorías"
    default_html_na = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Hola querida aplicante,</p>"
        "<p>Gracias por tomarte el tiempo de completar la segunda aplicación para nuestro programa de mentorías. "
        "Valoramos muchísimo tu interés en ser parte de Club Emprendo y el deseo que tienes de crecer y aprender.</p>"
        "<p>Después de revisar cuidadosamente tu información, queremos contarte que en esta ocasión no pudimos seleccionarte para este grupo. "
        "Esto se debe a que, según tu aplicación, actualmente se presenta al menos una de estas situaciones:</p>"
        "<ul>"
        "<li>Cuentas con menos de 2 horas de disponibilidad semanal para las mentorías.</li>"
        "<li>Tienes dificultades con la conexión a internet, lo cual es clave para poder comunicarse con tu mentora.</li>"
        "<li>Tu emprendimiento se encuentra aún en etapa de idea y no está en marcha. (si estás aplicando para recibir las mentorías)</li>"
        "<li>No cumples con los requisitos o disponibilidad.</li>"
        "</ul>"
        "<p>Para que el proceso de mentoría sea realmente efectivo y beneficioso para ti, en este grupo necesitamos que las emprendedoras "
        "cuenten con más de 2 horas de disponibilidad, buena conexión a internet y un emprendimiento ya en funcionamiento.</p>"
        "<p>La buena noticia es que, si en el futuro estas condiciones cambian, puedes volver a aplicar sin ningún problema.</p>"
        "<p>Gracias por confiar en Club Emprendo y por dar este primer paso. Te enviamos un abrazo grande y mucho ánimo en tu camino emprendedor.</p>"
        "<p>Con cariño,<br><strong>Equipo Club Emprendo</strong></p>"
        "</div>"
    )

    default_subject_ok = "Hemos recibido tu aplicación – Programa de Mentorías"
    intro_ok = (
        "Gracias por completar tu aplicación para ser mentora en nuestro Programa de Mentorías."
        if slug.endswith("M_A2")
        else "Gracias por completar tu aplicación para recibir mentoría en nuestro Programa de Mentorías."
    )
    default_html_ok = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Hola querida aplicante ✨</p>"
        f"<p>{intro_ok}</p>"
        "<p>Tu aplicación ha sido enviada correctamente y no necesitas realizar ninguna acción adicional por ahora.</p>"
        "<p>En la fecha indicada dentro de la aplicación, recibirás un correo electrónico en esta misma dirección únicamente si eres seleccionada, "
        "con los siguientes pasos a seguir.</p>"
        "<p>Te recomendamos estar pendiente de tu correo, incluyendo la bandeja de spam o promociones, para no perder esta información importante.</p>"
        "<p>Si eres seleccionada, deberás completar dentro de la fecha límite que se te indicará:</p>"
        "<ul>"
        "<li>✅ Firmar el Acta de Compromiso</li>"
        "<li>✅ Completar la capacitación</li>"
        "</ul>"
        "<p>Estos pasos son necesarios para poder asignarte una mentora y participar en la reunión de lanzamiento e inicio de mentorías.</p>"
        "<p>Gracias por tu interés en ser parte de esta comunidad 💗</p>"
        "<p>Con gratitud,<br><strong>Equipo de Club Emprendo</strong></p>"
        "</div>"
    )

    if send_disqualified_email:
        subject = resolve_form_email_template(
            form_def=app.form,
            field_name="email_a2_rejected_subject",
            default_text=default_subject_na,
            replacements=replacements,
            is_subject=True,
        )
        html_body = resolve_form_email_template(
            form_def=app.form,
            field_name="email_a2_rejected_body",
            default_text=default_html_na,
            replacements=replacements,
        )
    else:
        subject = resolve_form_email_template(
            form_def=app.form,
            field_name="email_a2_received_subject",
            default_text=default_subject_ok,
            replacements=replacements,
            is_subject=True,
        )
        html_body = resolve_form_email_template(
            form_def=app.form,
            field_name="email_a2_received_body",
            default_text=default_html_ok,
            replacements=replacements,
        )

    try:
        _send_html_email(
            to_email,
            subject,
            html_body,
        )
    except Exception:
        logger.exception(
            "Failed sending A2 submit email (slug=%s, app_id=%s, email=%s, disqualified=%s)",
            slug,
            app.id,
            to_email,
            send_disqualified_email,
        )

    return send_disqualified_email


def _apply_question_conditions(form):
    """
    Toggle required flags for conditional questions based on current form data.
    Fields carry `show_if_question` and `show_if_value` in widget attrs.
    """
    data = getattr(form, "data", None)

    def _value_for_field(fname: str):
        if data is not None:
            if hasattr(data, "getlist"):
                vals = data.getlist(fname)
                if len(vals) > 1:
                    return vals
                if fname in data:
                    return data.get(fname)
            else:
                if fname in data:
                    return data.get(fname)
        return form.initial.get(fname, "")

    def _matches(expected: str, raw_val):
        expected = (expected or "").strip().lower()
        if not expected:
            return True

        def truthy(v: str) -> bool:
            v = (v or "").strip().lower()
            return v in {"1", "true", "yes", "si", "sí", "on"}

        if isinstance(raw_val, list):
            vals = [str(v).strip().lower() for v in raw_val]
            if expected == "yes":
                return any(truthy(v) for v in vals)
            if expected == "no":
                return any(not truthy(v) for v in vals)
            return expected in vals

        val = (str(raw_val or "")).strip().lower()
        if expected == "yes":
            return truthy(val)
        if expected == "no":
            return not truthy(val)
        return val == expected

    for name, field in form.fields.items():
        base_required = getattr(field, "_ce_base_required", field.required)
        single_q = (field.widget.attrs.get("show_if_question") or "").strip()
        single_val = (field.widget.attrs.get("show_if_value") or "").strip()
        conds_raw = field.widget.attrs.get("show_if_conditions")
        conds = []
        if conds_raw:
            try:
                conds = json.loads(conds_raw)
            except Exception:
                conds = []

        def cond_matches(cond):
            fname = cond.get("field") or ""
            expected = (cond.get("value") or "").strip()
            if not fname or not expected:
                return False
            raw_val = _value_for_field(fname)
            return _matches(expected, raw_val)

        match = False
        if single_q and single_val:
            raw_val = _value_for_field(single_q)
            match = _matches(single_val, raw_val)

        if conds:
            match = match or any(cond_matches(c) for c in conds)

        field.required = base_required if match or (not single_q and not conds) else False


def _mentor_a1_autograde_and_email(request, app: Application):
    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    is_eligible = _mentor_a1_is_eligible(answers)
    if is_eligible:
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save(update_fields=["invite_token", "invited_to_second_stage"])
        return

    app.invited_to_second_stage = False
    app.save(update_fields=["invited_to_second_stage"])

    default_subject = "Sobre tu aplicación como mentora voluntaria 🌟"
    default_html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida aplicante a mentora,</p>"
        "<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. 💛</p>"
        "<p>En la aplicación indicaste que no cumples uno o más requisitos o disponibilidad para esta cohorte, por eso no podremos enviarte el paso 2.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    replacements = build_form_email_context(
        form_def=app.form,
        role_word="mentora",
        deadline=getattr(getattr(app.form, "group", None), "a2_deadline", None),
    )
    subject = resolve_form_email_template(
        form_def=app.form,
        field_name="email_a1_rejected_subject",
        default_text=default_subject,
        replacements=replacements,
        is_subject=True,
    )
    html_body = resolve_form_email_template(
        form_def=app.form,
        field_name="email_a1_rejected_body",
        default_text=default_html_body,
        replacements=replacements,
    )
    _send_html_email(app.email, subject, html_body)


def _mentor_a1_is_eligible(answers: dict[str, str]) -> bool:
    answers = {
        k: (v or "")
        for k, v in (answers or {}).items()
    }

    def yesish(v: str) -> bool:
        t = (v or "").strip().lower()
        return ("si" in t) or ("sí" in t) or ("yes" in t) or (t == "true") or (t == "1") or (t == "yes")

    requisitos = (
        answers.get("meets_requirements")
        or answers.get("m1_meet_requirements")
        or answers.get("m1_meets_requirements")
        or answers.get("m1_requirements_ok")
        or ""
    )
    disponibilidad = (
        answers.get("available_period")
        or answers.get("availability_ok")
        or answers.get("m1_availability_ok")
        or answers.get("m1_available_period")
        or answers.get("m1_available")
        or ""
    )

    return yesish(requisitos) and yesish(disponibilidad)


def _m2_sections(form_def: FormDefinition):
    def find_all_fragments(fragments):
        out = []
        for q in form_def.questions.filter(active=True).order_by("position", "id"):
            t = (q.text or "").lower()
            if all(f.lower() in t for f in fragments):
                out.append(f"q_{q.slug}")
        return out

    def find_any_keywords(keywords):
        out = []
        for q in form_def.questions.filter(active=True).order_by("position", "id"):
            t = (q.text or "").lower()
            if any(k.lower() in t for k in keywords):
                out.append(f"q_{q.slug}")
        return out

    owned_business_fields = find_all_fragments(["has dirigido tu propio negocio"])
    owned_business_field = owned_business_fields[0] if owned_business_fields else None

    sections = [
        {
            "key": "s1",
            "title": "Información personal",
            "intro": (
                "Solicitamos tu número de cédula únicamente para identificar de forma única tu postulación y evitar aplicaciones duplicadas.\n\n"
                "Tu información será utilizada exclusivamente para fines administrativos del programa de mentoría y tratada con estricta confidencialidad, conforme a la legislación de protección de datos personales vigente en tu país.\n\n"
                "🛡 Aviso de privacidad:\n"
                "Club Emprendo recopila datos personales limitados, como tu nombre y número de cédula, con fines administrativos relacionados con el proceso de postulación.\n"
                "Nos comprometemos a tratar esta información de forma confidencial, segura y conforme a las leyes de protección de datos aplicables en América Latina.\n"
                "Puedes ejercer tus derechos de acceso, corrección o eliminación de datos escribiéndonos a: contacto@clubemprendo.org"
            ),
            "field_names": find_any_keywords([
                "cédula", "cedula", "documento de identidad",
                "nombre completo",
                "nombre de preferencia",
                "certificado",
                "correo electrónico", "correo electronico",
                "whatsapp",
                "ciudad de residencia",
                "país de residencia", "pais de residencia",
                "país de nacimiento", "pais de nacimiento",
                "edad",
                "participado anteriormente",
                "aviso de privacidad",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s2",
            "title": "Requisitos del programa",
            "intro": "",
            "field_names": find_any_keywords([
                "requisitos básicos", "requisitos basicos",
                "requisitos de disponibilidad",
                "marca la casilla",
                "confirmar tu entendimiento",
                "si no cumples",
                "especifica cuál", "especifica cual",
                "revisaste el pdf",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s3",
            "title": "Experiencia previa",
            "intro": "",
            "field_names": owned_business_fields,
            "show_if_field": None,
        },
        {
            "key": "s4",
            "title": "Experiencia como emprendedora",
            "intro": (
                "En esta sección, te solicitamos que compartas tu experiencia previa como emprendedor(a). "
                "Responde a las preguntas sobre los negocios que has dirigido, centrándote en tu negocio favorito o "
                "más notable si has gestionado más de uno. Tu experiencia será valiosa para ayudar a nuestras "
                "microemprendedoras a crecer y superar desafíos"
            ),
            "field_names": find_any_keywords([
                "nombre de tu emprendimiento",
                "industria de tu emprendimiento",
                "descripción del negocio", "descripcion del negocio",
                "dónde operas tu negocio", "donde operas tu negocio",
                "cuánto tiempo has estado operando", "cuanto tiempo has estado operando",
                "tienes empleados",
            ]),
            "show_if_field": owned_business_field,
        },
        {
            "key": "s5",
            "title": "Motivación y experiencia con la mentoría",
            "intro": (
                "💡 Tip importante:\n"
                "En las preguntas abiertas, te recomendamos que seas lo más amplia posible al compartir tu experiencia, "
                "motivaciones y visión. 📝✨ Evita responder solo con una o dos frases — ¡queremos conocerte mejor para "
                "valorar todo lo que puedes aportar como mentora!"
            ),
            "field_names": find_any_keywords([
                "área de experiencia profesional", "area de experiencia profesional",
                "qué te motiva", "que te motiva",
                "buena mentora",
                "experiencia previa con mentoría", "experiencia previa con mentoria",
                "describe brevemente tu experiencia",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s6",
            "title": "Disponibilidad",
            "intro": "",
            "field_names": find_any_keywords([
                "cuánto tiempo puedes dedicar", "cuanto tiempo puedes dedicar",
                "en qué horario te resulta más conveniente", "en que horario te resulta mas conveniente",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s8",
            "title": "Comentarios adicionales",
            "intro": "Este espacio es tuyo: comentarios, dudas, sugerencias o algo que no hayamos preguntado.",
            "field_names": find_any_keywords([
                "hay algo más que te gustaría compartir",
                "hay algo mas que te gustaria compartir",
            ]),
            "show_if_field": None,
        },
    ]

    for s in sections:
        seen = set()
        deduped = []
        for n in s["field_names"]:
            if n and n not in seen:
                seen.add(n)
                deduped.append(n)
        s["field_names"] = deduped

    sections = [s for s in sections if s["field_names"]]
    return sections, owned_business_field


def _sections_from_model(form_def: FormDefinition, form, default_intro: str = ""):
    """
    Build a list of section dictionaries using Section model assignments.
    """
    sections_qs = list(
        form_def.sections.select_related("show_if_question", "show_if_question_2").order_by("position", "id")
    )
    if not sections_qs:
        return None

    q_by_id = {q.id: q for q in form_def.questions.all()}
    referenced_question_ids = set()
    for section in sections_qs:
        referenced_question_ids.update(
            int(c.get("question_id"))
            for c in (getattr(section, "show_if_conditions", None) or [])
            if c.get("question_id")
        )
        referenced_question_ids.update(
            qid for qid in (section.show_if_question_id, section.show_if_question_2_id) if qid
        )
    if referenced_question_ids:
        q_by_id.update({q.id: q for q in Question.objects.filter(id__in=referenced_question_ids)})

    section_map = {}
    for s in sections_qs:
        def build_cond(qid, val):
            if qid and qid in q_by_id and val:
                return {
                    "question_id": qid,
                    "field_name": f"q_{q_by_id[qid].slug}",
                    "value": (val or "").strip(),
                    "field_type": q_by_id[qid].field_type,
                }
            return None

        conds = []
        if getattr(s, "show_if_conditions", None):
            for c in s.show_if_conditions:
                qid = c.get("question_id")
                val = c.get("value")
                cond = build_cond(qid, val)
                if cond:
                    conds.append(cond)
        else:
            cond1 = build_cond(s.show_if_question_id, s.show_if_value)
            cond2 = build_cond(s.show_if_question_2_id, s.show_if_value_2)
            for c in (cond1, cond2):
                if c:
                    conds.append(c)

        section_map[s.id] = {
            "id": s.id,
            "title": s.title,
            "intro": s.description,
            "show_if_logic": s.show_if_logic,
            "conditions": conds,
            "conditions_json": json.dumps(conds),
            "show_if_field_name": conds[0]["field_name"] if len(conds) > 0 else "",
            "show_if_value": conds[0]["value"] if len(conds) > 0 else "",
            "show_if_field_name_2": conds[1]["field_name"] if len(conds) > 1 else "",
            "show_if_value_2": conds[1]["value"] if len(conds) > 1 else "",
            "fields": [],
        }

    default_bucket = {
        "id": "unassigned",
        "title": form_def.default_section_title or "Preguntas generales",
        "intro": str(default_intro or "").strip(),
        "fields": [],
    }

    for field in form:
        source_form_id = field.field.widget.attrs.get("source_form_id") if hasattr(field.field, "widget") else ""
        if source_form_id and str(source_form_id) != str(form_def.id):
            continue
        raw = field.field.widget.attrs.get("section_id") if hasattr(field.field, "widget") else ""
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            sid = None

        if sid and sid in section_map:
            section_map[sid]["fields"].append(field)
        else:
            default_bucket["fields"].append(field)

    # Apply conditional visibility (show_if_question + value)
    def _value_for_field(fname: str):
        data = getattr(form, "data", None)
        if data is not None:
            if hasattr(data, "getlist"):
                vals = data.getlist(fname)
                if len(vals) > 1:
                    return vals
                if fname in data:
                    return data.get(fname)
            else:
                if fname in data:
                    return data.get(fname)
        return form.initial.get(fname, "")

    filtered = []
    for bucket in ([default_bucket] + [section_map[s.id] for s in sections_qs]):
        conditions = bucket.get("conditions", [])
        logic = (bucket.get("show_if_logic") or "AND")
        if isinstance(logic, str):
            logic = logic.strip().upper()
        else:
            logic = "AND"

        def matches(cond):
            fname = cond["field_name"]
            expected = (cond["value"] or "").strip().lower()
            raw_val = _value_for_field(fname)

            def truthy(v: str) -> bool:
                v = (v or "").strip().lower()
                return v in {"1", "true", "yes", "si", "sí", "on"}

            exp_truthy = expected in {"1", "true", "yes", "si", "sí", "on"}
            exp_falsey = expected in {"0", "false", "no", "off"}

            if isinstance(raw_val, list):
                vals = [str(v).strip().lower() for v in raw_val]
                if exp_truthy:
                    return any(truthy(v) for v in vals)
                if exp_falsey:
                    return all(not truthy(v) for v in vals)
                return expected in vals

            val = (str(raw_val or "")).strip().lower()
            if exp_truthy:
                return truthy(val)
            if exp_falsey:
                return not truthy(val)
            return val == expected

        if conditions:
            results = [matches(c) for c in conditions]
            visible = any(results) if logic == "OR" else all(results)
            if not visible:
                for f in bucket["fields"]:
                    f.field.required = False
            bucket["hidden"] = not visible
        filtered.append(bucket)

    ordered = [b for b in filtered if b["fields"]]
    if not ordered:
        return None
    return ordered


def _group_num_from_slug(slug: str) -> str:
    m = GROUP_SLUG_RE.match(slug or "")
    return m.group("num") if m else ""


def _a1_track_from_slug(slug: str) -> str:
    if (slug or "").endswith("M_A1"):
        return "mentoras"
    if (slug or "").endswith("E_A1"):
        return "emprendedoras"
    return ""


def _track_from_slug(slug: str) -> str:
    s = (slug or "").strip()
    if s.endswith("E_A1") or s.endswith("E_A2"):
        return "emprendedoras"
    if s.endswith("M_A1") or s.endswith("M_A2"):
        return "mentoras"
    return ""


def _render_thanks_text_template(
    raw_text: str,
    *,
    group_num: str,
    track: str,
    form_name: str,
) -> str:
    text = str(raw_text or "")
    if not text.strip():
        return ""

    num = str(group_num or "").strip()
    track_key = (track or "").strip().lower()
    if not track_key:
        track_label = "Club Emprendo"
    elif track_key.startswith("e"):
        track_label = "emprendedoras"
    elif track_key.startswith("m"):
        track_label = "mentoras"
    else:
        track_label = track_key

    replacements = {
        "group_num": num,
        "group_label": f"Grupo {num}" if num else "Grupo #",
        "track": track_label,
        "track_label": track_label,
        "form_name": str(form_name or "").strip(),
    }

    def _replace(match: re.Match) -> str:
        key = (match.group(1) or "").strip()
        return replacements.get(key, match.group(0))

    return THANKS_PLACEHOLDER_RE.sub(_replace, text)


def _thanks_override_payload(
    *,
    form_def: FormDefinition,
    kind: str,
    approved: bool,
    disqualified: bool,
    group_num: str,
    track: str,
) -> dict[str, str]:
    is_positive = bool(approved)
    if kind in {"mentor_final", "emprendedora_final"}:
        is_positive = not bool(disqualified)

    raw_title = (
        (form_def.thanks_approved_title if is_positive else form_def.thanks_rejected_title)
        if form_def
        else ""
    )
    raw_message = (
        (form_def.thanks_approved_message if is_positive else form_def.thanks_rejected_message)
        if form_def
        else ""
    )
    if not str(raw_message or "").strip():
        return {}

    track_value = (track or "").strip() or _track_from_slug(getattr(form_def, "slug", "") or "")
    rendered_title = _render_thanks_text_template(
        raw_title,
        group_num=group_num,
        track=track_value,
        form_name=getattr(form_def, "name", "") or "",
    ).strip()
    rendered_message = _render_thanks_text_template(
        raw_message,
        group_num=group_num,
        track=track_value,
        form_name=getattr(form_def, "name", "") or "",
    ).strip()
    if not rendered_message:
        return {}

    return {
        "custom_message_title": rendered_title,
        "custom_message_body": rendered_message,
        "custom_message_variant": "alert" if kind == "a1" and is_positive else "intro",
    }


def _slug_uses_combined_flow(slug: str) -> bool:
    fd = (
        FormDefinition.objects.select_related("group")
        .filter(slug=slug)
        .first()
    )
    if not fd:
        return False
    return bool(getattr(getattr(fd, "group", None), "use_combined_application", False))


def _resolve_a2_slug_from_first_app(first_app: Application, role: str) -> str:
    """
    role: "E" or "M"
    """
    if role == "E":
        form_slug = "E_A2"
        target_master = "E_A2"
    else:
        form_slug = "M_A2"
        target_master = "M_A2"

    grouped_slug = _group_form_slug_for_master(getattr(first_app.form, "group", None), target_master)
    if grouped_slug:
        return grouped_slug

    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_{target_master}"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate
    return form_slug


def _combined_second_form(first_form: FormDefinition) -> FormDefinition | None:
    slug = first_form.slug or ""
    if slug.endswith("E_A1"):
        master_slug = "E_A2"
    elif slug.endswith("M_A1"):
        master_slug = "M_A2"
    else:
        return None
    second_slug = _group_form_slug_for_master(getattr(first_form, "group", None), master_slug) or master_slug
    return FormDefinition.objects.filter(slug=second_slug).first()


def _combined_sections(first_form, second_form, form, first_intro=""):
    sections = []
    for index, (form_def, intro) in enumerate(((first_form, first_intro), (second_form, second_form.description or ""))):
        part = _sections_from_model(form_def, form, default_intro=intro) or []
        if not part:
            fields = [
                field for field in form
                if str(field.field.widget.attrs.get("source_form_id") or "") == str(form_def.id)
            ]
            if fields:
                part = [{
                    "id": f"combined-{form_def.id}",
                    "title": form_def.default_section_title or f"Parte {index + 1}",
                    "intro": intro,
                    "fields": fields,
                }]
        sections.extend(part)
    return sections or None


def _invite_app_for_a2_token(token: str, target_form_slug: str) -> Application | None:
    raw_token = (token or "").strip()
    slug = (target_form_slug or "").strip()
    if not raw_token:
        return None
    if not (slug.endswith("E_A2") or slug.endswith("M_A2")):
        return None

    app = (
        Application.objects.select_related("form")
        .filter(invite_token=raw_token)
        .first()
    )
    if not app:
        return None

    source_slug = (getattr(getattr(app, "form", None), "slug", "") or "").strip()
    if slug.endswith("E_A2"):
        return app if (source_slug.endswith("E_A1") or source_slug.endswith("E_A2")) else None
    if slug.endswith("M_A2"):
        return app if (source_slug.endswith("M_A1") or source_slug.endswith("M_A2")) else None
    return None


def _matching_end_form_rule(post_data, form_defs):
    for form_def in form_defs:
        for question in form_def.questions.filter(active=True).order_by("position", "id"):
            rules = list(getattr(question, "end_form_rules", []) or [])
            if not rules:
                continue
            field_name = f"q_{question.slug}"
            values = (
                post_data.getlist(field_name)
                if hasattr(post_data, "getlist")
                else [post_data.get(field_name)]
            )
            normalized = {str(value or "").strip().lower() for value in values}
            for rule in rules:
                expected = str(rule.get("value") or "").strip().lower()
                if expected and expected in normalized:
                    return question, rule
    return None, None


def _send_stored_email_for_rule(app, question, rule):
    email_name = str(rule.get("email_name") or "").strip()
    recipient = (app.email or "").strip()
    if not email_name or not recipient:
        return False
    template = question.form.stored_emails.filter(name=email_name).first()
    if not template:
        return False
    replacements = build_form_email_context(form_def=question.form)
    replacements.update({"name": app.name or "", "email": recipient})
    subject = " ".join(render_email_template(template.subject, replacements).splitlines()).strip()
    body = render_email_template(template.body, replacements)
    EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[recipient],
    ).send(fail_silently=False)
    return True


def _handle_application_form(
    request,
    form_slug: str,
    second_stage: bool = False,
    combined_flow: bool = False,
    invite_token: str | None = None,
):
    _maybe_run_due_a1_to_a2_reminders()
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
    reuse_token = (invite_token or request.GET.get("token") or "").strip()
    invite_source_app = _invite_app_for_a2_token(reuse_token, form_def.slug)
    has_invite_access = invite_source_app is not None
    if reuse_token and (form_def.slug.endswith("E_A2") or form_def.slug.endswith("M_A2")) and not has_invite_access:
        raise Http404("Invalid invite token.")

    is_admin_preview = (
        request.method == "GET"
        and bool(getattr(request.user, "is_staff", False))
        and (request.GET.get("preview") or "").strip().lower() in {"1", "true", "yes"}
    )

    manual_override = getattr(form_def, "manual_open_override", None)
    desired_open = None
    if manual_override is not None:
        desired_open = bool(manual_override)
    else:
        desired_open = scheduled_group_open_state(getattr(form_def, "group", None))

    if desired_open is not None:
        if desired_open != form_def.is_public or desired_open != getattr(form_def, "accepting_responses", desired_open):
            FormDefinition.objects.filter(id=form_def.id).update(
                is_public=desired_open,
                accepting_responses=desired_open,
            )
            form_def.is_public = desired_open
            form_def.accepting_responses = desired_open

    # Block new submissions when closed (we use is_public as "open" flag)
    if not is_admin_preview and not has_invite_access and not form_def.is_public:
        return render(
            request,
            "applications/closed.html",
            {"form_def": form_def},
            status=403,
        )


    if (
        request.method == "POST"
        and not has_invite_access
        and not getattr(form_def, "accepting_responses", True)
    ):
        return render(
            request,
            "applications/form_closed.html",
            {
                "form_def": form_def,
                "second_stage": second_stage,
            },
            status=403,
        )

    # New combined submissions are one form and one database application. Token-based
    # requests remain on the legacy continuation path for already-started applications.
    combined_second_def = (
        _combined_second_form(form_def)
        if combined_flow and not reuse_token and _is_a1_form_slug(form_def.slug or "")
        else None
    )
    single_combined_submission = combined_second_def is not None
    ApplicationForm = build_application_form(
        form_slug,
        additional_form_slugs=[combined_second_def.slug] if combined_second_def else None,
    )

    rendered_description = ""
    for attr in ("description", "intro", "intro_text", "public_description"):
        if hasattr(form_def, attr):
            v = getattr(form_def, attr) or ""
            if str(v).strip():
                rendered_description = str(v)
                break

    submission_form_defs = [form_def] + ([combined_second_def] if combined_second_def else [])
    terminal_question = None
    terminal_rule = None
    if request.method == "POST":
        form = ApplicationForm(request.POST)
        terminal_question, terminal_rule = _matching_end_form_rule(request.POST, submission_form_defs)
    else:
        form = ApplicationForm()

    _apply_question_conditions(form)
    if terminal_rule:
        for field in form.fields.values():
            field.required = False

    if single_combined_submission:
        sections = _combined_sections(
            form_def,
            combined_second_def,
            form,
            first_intro=rendered_description,
        )
        rendered_description = ""
    else:
        sections = _sections_from_model(form_def, form, default_intro=rendered_description)
    if sections:
        default_with_fields = next(
            (
                s for s in sections
                if s.get("id") == "unassigned" and s.get("fields")
            ),
            None,
        )
        if default_with_fields and (default_with_fields.get("intro") or "").strip():
            # Keep description with the default section block instead of duplicating it above.
            rendered_description = ""
    m2_gate_field = None

    # Legacy: fallback to heuristic sections for Mentora A2 if no explicit sections exist
    if not sections and (form_def.slug or "").endswith("M_A2"):
        raw_sections, gate = _m2_sections(form_def)
        m2_gate_field = gate

        sections = []
        for s in raw_sections:
            bound = []
            for fname in s["field_names"]:
                if fname in form.fields:
                    bound.append(form[fname])
            if bound:
                sections.append({
                    "key": s["key"],
                    "title": s["title"],
                    "intro": s["intro"],
                    "show_if_field": s["show_if_field"],
                    "fields": bound,
                })

    if request.method == "POST" and form.is_valid():

        def _pick_first(*keys: str) -> str:
            for k in keys:
                v = (form.cleaned_data.get(k) or "").strip()
                if v:
                    return v
            return ""

        full_name = _pick_first(
            "q_full_name", "q_name", "q_nombre",
            "q_e1_full_name", "q_m1_full_name",
            "q_e2_full_name", "q_m2_full_name",
        )
        email = _pick_first(
            "q_email", "q_correo", "q_correo_electronico",
            "q_e1_email", "q_m1_email",
            "q_e2_email", "q_m2_email",
        )

        if not email:
            for k, v in form.cleaned_data.items():
                if not k.startswith("q_"):
                    continue
                s = (v or "").strip()
                if "@" in s and "." in s:
                    email = s
                    break

        if not full_name:
            for k, v in form.cleaned_data.items():
                if not k.startswith("q_"):
                    continue
                lk = k.lower()
                if ("name" in lk) or ("nombre" in lk):
                    s = (v or "").strip()
                    if s:
                        full_name = s
                        break

        existing_app = None
        if reuse_token and (form_def.slug.endswith("E_A2") or form_def.slug.endswith("M_A2")):
            existing_app = invite_source_app
            if not existing_app:
                raise Http404("Invalid invite token.")

            existing_slug = existing_app.form.slug or ""
            if form_def.slug.endswith("E_A2"):
                valid_source = existing_slug.endswith("E_A1") or existing_slug.endswith("E_A2")
            else:
                valid_source = existing_slug.endswith("M_A1") or existing_slug.endswith("M_A2")
            if not valid_source:
                raise Http404("Invite token does not match this application type.")

        with transaction.atomic():
            if existing_app is not None:
                app = existing_app
                app.form = form_def
                app.name = full_name
                app.email = email
                app.invited_to_second_stage = True
                app.second_stage_reminder_due_at = None
                app.second_stage_reminder_sent_at = timezone.now()
                app.save(
                    update_fields=[
                        "form",
                        "name",
                        "email",
                        "invited_to_second_stage",
                        "second_stage_reminder_due_at",
                        "second_stage_reminder_sent_at",
                    ]
                )
                Answer.objects.filter(application=app).delete()
            else:
                app = Application.objects.create(
                    form=form_def,
                    name=full_name,
                    email=email,
                )

            answer_map: dict[str, str] = {}
            submission_forms = [form_def]
            if combined_second_def:
                submission_forms.append(combined_second_def)
            submission_questions = []
            for submission_form in submission_forms:
                submission_questions.extend(
                    submission_form.questions.filter(active=True).order_by("position", "id")
                )
            for q in submission_questions:
                field_name = f"q_{q.slug}"
                value = form.cleaned_data.get(field_name)
                if isinstance(value, list):
                    value = ",".join(value)
                stored_value = str(value or "")
                Answer.objects.create(
                    application=app,
                    question=q,
                    value=stored_value,
                )
                answer_map[q.slug] = stored_value

        if terminal_rule and terminal_question:
            try:
                _send_stored_email_for_rule(app, terminal_question, terminal_rule)
            except Exception:
                logger.exception("Stored terminal email failed for application %s", app.pk)
            return render(
                request,
                "applications/thanks.html",
                {
                    "custom_message_title": str(terminal_rule.get("page_title") or "").strip(),
                    "custom_message_body": str(terminal_rule.get("page_message") or "").strip(),
                    "custom_message_variant": "alert",
                },
            )

        try:
            gnum_raw = _group_num_from_form_def(form_def)
            if gnum_raw:
                track = "M" if (form_def.slug or "").endswith("M_A1") or (form_def.slug or "").endswith("M_A2") else "E"
                schedule_group_track_responses_sync(int(gnum_raw), track)
        except Exception:
            logger.exception("Drive response CSV sync trigger failed for form %s", form_def.slug)

        a2_disqualified = False
        if form_def.slug.endswith("E_A2") or form_def.slug.endswith("M_A2"):
            a2_disqualified = _is_a2_na_candidate(form_def.slug or "", answer_map)
            _send_a2_submission_email(app, answer_map)

        if combined_flow and (form_def.slug.endswith("M_A1") or form_def.slug.endswith("E_A1")):
            if form_def.slug.endswith("M_A1"):
                passed = _mentor_a1_is_eligible(answer_map)
            else:
                passed = emprendedora_a1_passes(answer_map)

            if passed and single_combined_submission:
                # Preserve A1 grading/email behavior, then promote this same row to the
                # final form. Its Answer rows retain both A1 and A2 questions.
                if form_def.slug.endswith("M_A1"):
                    _mentor_a1_autograde_and_email(request, app)
                else:
                    autograde_and_email_emprendedora_a1(request, app)
                app.refresh_from_db()
                app.form = combined_second_def
                app.invited_to_second_stage = True
                app.second_stage_reminder_due_at = None
                app.second_stage_reminder_sent_at = timezone.now()
                app.save(update_fields=[
                    "form",
                    "invited_to_second_stage",
                    "second_stage_reminder_due_at",
                    "second_stage_reminder_sent_at",
                ])
                form_def = combined_second_def
                a2_disqualified = _is_a2_na_candidate(form_def.slug or "", answer_map)
                _send_a2_submission_email(app, answer_map)
                try:
                    gnum_raw = _group_num_from_form_def(form_def)
                    if gnum_raw:
                        schedule_group_track_responses_sync(
                            int(gnum_raw),
                            "M" if form_def.slug.endswith("M_A2") else "E",
                        )
                except Exception:
                    logger.exception("Drive response CSV sync trigger failed for combined form %s", form_def.slug)
            elif passed:
                app.generate_invite_token()
                app.invited_to_second_stage = True
                app.save(update_fields=["invite_token", "invited_to_second_stage"])
                app.refresh_from_db()
                _schedule_a1_to_a2_reminder(app)
                continue_url = f"{request.path}?combined=1&token={app.invite_token}"
                return redirect(continue_url)

            if not passed:
                # Reuse the original A1 rejection-email path for combined flow too.
                if form_def.slug.endswith("M_A1"):
                    _mentor_a1_autograde_and_email(request, app)
                else:
                    autograde_and_email_emprendedora_a1(request, app)
                app.refresh_from_db()
                _schedule_a1_to_a2_reminder(app)
                thanks_payload = {
                    "kind": "a1",
                    "approved": False,
                    "group_num": _group_num_from_form_def(form_def),
                    "track": _a1_track_from_slug(form_def.slug or ""),
                }
                thanks_payload.update(
                    _thanks_override_payload(
                        form_def=form_def,
                        kind=str(thanks_payload.get("kind") or ""),
                        approved=bool(thanks_payload.get("approved")),
                        disqualified=bool(thanks_payload.get("disqualified")),
                        group_num=str(thanks_payload.get("group_num") or ""),
                        track=str(thanks_payload.get("track") or ""),
                    )
                )
                return render(request, "applications/thanks.html", thanks_payload)

        # A1 autogrades
        if form_def.slug.endswith("M_A1"):
            _mentor_a1_autograde_and_email(request, app)
            app.refresh_from_db()
            _schedule_a1_to_a2_reminder(app)

        if form_def.slug.endswith("E_A1"):
            autograde_and_email_emprendedora_a1(request, app)
            app.refresh_from_db()
            _schedule_a1_to_a2_reminder(app)

        group_num = _group_num_from_form_def(form_def)

        # Track for rejection message
        track = _a1_track_from_slug(form_def.slug or "")

        # ✅ Thank-you routing
        if form_def.slug.endswith("M_A2"):
            thanks_payload = {
                "kind": "mentor_final",
                "group_num": group_num,
                "disqualified": a2_disqualified,
            }
        elif form_def.slug.endswith("E_A2"):
            thanks_payload = {
                "kind": "emprendedora_final",
                "group_num": group_num,
                "disqualified": a2_disqualified,
            }
        else:
            thanks_payload = {
                "kind": "a1",
                "approved": bool(app.invited_to_second_stage),
                "group_num": group_num,
                "track": track,
            }

        thanks_payload.update(
            _thanks_override_payload(
                form_def=form_def,
                kind=str(thanks_payload.get("kind") or ""),
                approved=bool(thanks_payload.get("approved")),
                disqualified=bool(thanks_payload.get("disqualified")),
                group_num=str(thanks_payload.get("group_num") or ""),
                track=str(thanks_payload.get("track") or ""),
            )
        )

        if combined_flow:
            return render(request, "applications/thanks.html", thanks_payload)
        request.session["ce_thanks_payload"] = thanks_payload
        return redirect("application_thanks")

    return render(
        request,
        "applications/application_form.html",
        {
            "form": form,
            "form_def": form_def,
            "second_stage": second_stage,
            "combined_flow": combined_flow,
            "display_form_name": None,
            "rendered_description": rendered_description,
            "sections": sections,
            "m2_gate_field": m2_gate_field,
        },
    )


def apply_emprendedora_first(request):
    token = (request.GET.get("token") or "").strip()
    if token:
        first_app = get_object_or_404(Application, invite_token=token)
        if not (first_app.form.slug or "").endswith("E_A1"):
            raise Http404("Invalid combined token for emprendedora flow.")
        a2_slug = _resolve_a2_slug_from_first_app(first_app, role="E")
        return _handle_application_form(
            request,
            a2_slug,
            second_stage=False,
            combined_flow=True,
            invite_token=token,
        )

    latest = _latest_group_form_slug("E_A1", public_only=True)
    target_slug = latest or "E_A1"
    return _handle_application_form(
        request,
        target_slug,
        second_stage=False,
        combined_flow=_slug_uses_combined_flow(target_slug),
    )


def apply_mentora_first(request):
    token = (request.GET.get("token") or "").strip()
    if token:
        first_app = get_object_or_404(Application, invite_token=token)
        if not (first_app.form.slug or "").endswith("M_A1"):
            raise Http404("Invalid combined token for mentora flow.")
        a2_slug = _resolve_a2_slug_from_first_app(first_app, role="M")
        return _handle_application_form(
            request,
            a2_slug,
            second_stage=False,
            combined_flow=True,
            invite_token=token,
        )

    latest = _latest_group_form_slug("M_A1", public_only=True)
    target_slug = latest or "M_A1"
    return _handle_application_form(
        request,
        target_slug,
        second_stage=False,
        combined_flow=_slug_uses_combined_flow(target_slug),
    )


def apply_emprendedora_combined(request):
    token = (request.GET.get("token") or "").strip()
    if token:
        first_app = get_object_or_404(Application, invite_token=token)
        if not (first_app.form.slug or "").endswith("E_A1"):
            raise Http404("Invalid combined token for emprendedora flow.")
        a2_slug = _resolve_a2_slug_from_first_app(first_app, role="E")
        return _handle_application_form(
            request,
            a2_slug,
            second_stage=False,
            combined_flow=True,
            invite_token=token,
        )

    latest = _latest_group_form_slug("E_A1", combined_only=True, public_only=True)
    return _handle_application_form(
        request,
        latest or "E_A1",
        second_stage=False,
        combined_flow=True,
    )


def apply_mentora_combined(request):
    token = (request.GET.get("token") or "").strip()
    if token:
        first_app = get_object_or_404(Application, invite_token=token)
        if not (first_app.form.slug or "").endswith("M_A1"):
            raise Http404("Invalid combined token for mentora flow.")
        a2_slug = _resolve_a2_slug_from_first_app(first_app, role="M")
        return _handle_application_form(
            request,
            a2_slug,
            second_stage=False,
            combined_flow=True,
            invite_token=token,
        )

    latest = _latest_group_form_slug("M_A1", combined_only=True, public_only=True)
    return _handle_application_form(
        request,
        latest or "M_A1",
        second_stage=False,
        combined_flow=True,
    )


def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)
    form_slug = _resolve_a2_slug_from_first_app(first_app, role="E")

    combined_flow = (request.GET.get("combined") or "").strip().lower() in {"1", "true", "yes"}
    return _handle_application_form(
        request,
        form_slug,
        second_stage=not combined_flow,
        combined_flow=combined_flow,
        invite_token=token,
    )


def apply_mentora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)
    form_slug = _resolve_a2_slug_from_first_app(first_app, role="M")

    combined_flow = (request.GET.get("combined") or "").strip().lower() in {"1", "true", "yes"}
    return _handle_application_form(
        request,
        form_slug,
        second_stage=not combined_flow,
        combined_flow=combined_flow,
        invite_token=token,
    )


def apply_emprendedora_second_preview(request):
    latest = _latest_group_form_slug("E_A2")
    return _handle_application_form(request, latest or "E_A2", second_stage=True)


def apply_mentora_second_preview(request):
    latest = _latest_group_form_slug("M_A2")
    return _handle_application_form(request, latest or "M_A2", second_stage=True)


def apply_by_slug(request, form_slug):
    second_stage = str(form_slug).endswith("_A2")
    raw_combined = (request.GET.get("combined") or "").strip().lower()
    token = (request.GET.get("token") or "").strip()
    if raw_combined in {"1", "true", "yes"}:
        combined_flow = True
    elif raw_combined in {"0", "false", "no"}:
        combined_flow = False
    else:
        is_a1 = (form_slug or "").endswith("E_A1") or (form_slug or "").endswith("M_A1")
        combined_flow = _slug_uses_combined_flow(form_slug) if is_a1 else False

    if combined_flow and token and not second_stage and (
        (form_slug or "").endswith("E_A1") or (form_slug or "").endswith("M_A1")
    ):
        first_app = get_object_or_404(Application, invite_token=token)
        if (form_slug or "").endswith("E_A1"):
            if not (first_app.form.slug or "").endswith("E_A1"):
                raise Http404("Invalid combined token.")
            form_slug = _resolve_a2_slug_from_first_app(first_app, role="E")
        else:
            if not (first_app.form.slug or "").endswith("M_A1"):
                raise Http404("Invalid combined token.")
            form_slug = _resolve_a2_slug_from_first_app(first_app, role="M")
        second_stage = False

    return _handle_application_form(
        request,
        form_slug,
        second_stage=second_stage,
        combined_flow=combined_flow,
        invite_token=token or None,
    )


def application_thanks(request):
    payload = request.session.pop("ce_thanks_payload", None) or {}
    return render(request, "applications/thanks.html", payload)


def survey_by_slug(request, form_slug):
    return _handle_application_form(request, form_slug, second_stage=False)
