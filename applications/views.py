# applications/views.py
import re
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
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
from .emprendedora_a1_autograde import (
    autograde_and_email_emprendedora_a1,
    emprendedora_a1_passes,
)
from .grader_e import _disqualification_reasons as _e_a2_disqualification_reasons
from .grader_m import _disqualification_reasons as _m_a2_disqualification_reasons


GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")
logger = logging.getLogger(__name__)

MONTH_NUM_TO_ES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}
A1_TO_A2_REMINDER_DELAY_HOURS = 24
A1_TO_A2_REMINDER_CHECK_THROTTLE_SECONDS = 45
A1_TO_A2_REMINDER_RUN_LOCK_TTL_SECONDS = 60 * 15


# -------------------------
# Utilities
# -------------------------
def _latest_group_form_slug(suffix: str, combined_only: bool | None = None) -> str | None:
    pattern = re.compile(rf"^G(?P<num>\d+)_{re.escape(suffix)}$")
    best = None
    best_num = -1

    for fd in FormDefinition.objects.filter(slug__endswith=suffix).select_related("group"):
        m = pattern.match(fd.slug or "")
        if not m:
            continue
        if combined_only is not None:
            use_combined = bool(getattr(getattr(fd, "group", None), "use_combined_application", False))
            if combined_only and not use_combined:
                continue
            if not combined_only and use_combined:
                continue
        n = int(m.group("num"))
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


def _a2_candidate_slugs_for_a1_slug(a1_slug: str) -> list[str]:
    slug = a1_slug or ""
    if slug.endswith("E_A1"):
        master = "E_A2"
        grouped = slug.replace("_E_A1", "_E_A2") if slug.startswith("G") and "_E_A1" in slug else None
    elif slug.endswith("M_A1"):
        master = "M_A2"
        grouped = slug.replace("_M_A1", "_M_A2") if slug.startswith("G") and "_M_A1" in slug else None
    else:
        return []

    candidates: list[str] = []
    if grouped:
        candidates.append(grouped)
    candidates.append(master)
    return candidates


def _a2_completed_for_a1_app(app: Application) -> bool:
    email = (app.email or "").strip()
    if not email:
        return False

    candidate_slugs = _a2_candidate_slugs_for_a1_slug(app.form.slug or "")
    if not candidate_slugs:
        return False

    return Application.objects.filter(
        form__slug__in=candidate_slugs,
        email__iexact=email,
    ).exists()


def _second_stage_link_for_a1_app(app: Application) -> str:
    if not app.invite_token:
        app.generate_invite_token()
        app.save(update_fields=["invite_token"])

    if (app.form.slug or "").endswith("E_A1"):
        path = reverse("apply_emprendedora_second", kwargs={"token": str(app.invite_token)})
    else:
        path = reverse("apply_mentora_second", kwargs={"token": str(app.invite_token)})

    base_url = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        base_url = "https://apply.clubemprendo.org"
    return f"{base_url}{path}"


def _build_a1_to_a2_reminder_email(app: Application) -> tuple[str, str]:
    is_emprendedora = (app.form.slug or "").endswith("E_A1")
    role_word = "emprendedora" if is_emprendedora else "mentora"

    deadline_text = ""
    group = getattr(app.form, "group", None)
    deadline = getattr(group, "a2_deadline", None) if group else None
    if deadline:
        month = MONTH_NUM_TO_ES.get(deadline.month, "")
        if month:
            deadline_text = f"{deadline.day} de {month} de {deadline.year}"
        else:
            deadline_text = deadline.strftime("%d/%m/%Y")

    a2_link = _second_stage_link_for_a1_app(app)
    deadline_sentence = (
        f"<p>Te recordamos que la fecha límite para completar tu aplicación es el <strong>{deadline_text}</strong>.</p>"
        if deadline_text
        else ""
    )

    subject = "Recordatorio: completa tu segunda aplicación"
    html_body = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;">
      <p>Hola,</p>
      <p>
        Queremos recordarte que según tu primera aplicación fuiste invitada a continuar al segundo paso del
        proceso para participar como <strong>{role_word}</strong> en Club Emprendo.
      </p>
      {deadline_sentence}
      <p>
        Si aún no has completado la segunda aplicación, puedes hacerlo aquí:
        👉 <a href="{a2_link}">{a2_link}</a>
      </p>
      <p>Con cariño,<br><strong>Equipo Club Emprendo</strong></p>
    </div>
    """
    return subject, html_body


def _schedule_a1_to_a2_reminder(app: Application):
    slug = app.form.slug or ""
    if not _is_a1_form_slug(slug):
        return

    target_due_at = None
    if app.invited_to_second_stage:
        target_due_at = app.created_at + timedelta(hours=A1_TO_A2_REMINDER_DELAY_HOURS)

    update_fields: list[str] = []
    if app.second_stage_reminder_due_at != target_due_at:
        app.second_stage_reminder_due_at = target_due_at
        update_fields.append("second_stage_reminder_due_at")
    if app.second_stage_reminder_sent_at is not None:
        app.second_stage_reminder_sent_at = None
        update_fields.append("second_stage_reminder_sent_at")

    if update_fields:
        app.save(update_fields=update_fields)


def _mark_a1_reminder_sent(form_slug: str, email: str):
    now = timezone.now()
    Application.objects.filter(
        form__slug=form_slug,
        email__iexact=email,
        invited_to_second_stage=True,
        second_stage_reminder_sent_at__isnull=True,
    ).update(second_stage_reminder_sent_at=now)


def _run_due_a1_to_a2_reminders():
    now = timezone.now()
    due_apps = list(
        Application.objects.select_related("form", "form__group")
        .filter(
            invited_to_second_stage=True,
            second_stage_reminder_due_at__isnull=False,
            second_stage_reminder_due_at__lte=now,
            second_stage_reminder_sent_at__isnull=True,
        )
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .order_by("second_stage_reminder_due_at", "id")[:300]
    )

    processed_keys: set[tuple[str, str]] = set()
    for app in due_apps:
        email = (app.email or "").strip().lower()
        if not email:
            continue
        key = (app.form.slug or "", email)
        if key in processed_keys:
            continue
        processed_keys.add(key)

        try:
            fresh = (
                Application.objects.select_related("form", "form__group")
                .filter(
                    id=app.id,
                    invited_to_second_stage=True,
                    second_stage_reminder_due_at__isnull=False,
                    second_stage_reminder_due_at__lte=timezone.now(),
                    second_stage_reminder_sent_at__isnull=True,
                )
                .first()
            )
            if not fresh:
                continue

            if _a2_completed_for_a1_app(fresh):
                _mark_a1_reminder_sent(fresh.form.slug or "", fresh.email or "")
                continue

            subject, html_body = _build_a1_to_a2_reminder_email(fresh)
            _send_html_email((fresh.email or "").strip(), subject, html_body)
            _mark_a1_reminder_sent(fresh.form.slug or "", fresh.email or "")
        except Exception:
            logger.exception(
                "Automatic A1->A2 reminder failed (app_id=%s, form=%s, email=%s)",
                app.id,
                app.form.slug,
                app.email,
            )


def _maybe_run_due_a1_to_a2_reminders():
    gate_key = "public:reminders:a1_to_a2:check"
    if not cache.add(gate_key, "1", timeout=A1_TO_A2_REMINDER_CHECK_THROTTLE_SECONDS):
        return

    run_lock_key = "public:reminders:a1_to_a2:runlock"
    if not cache.add(run_lock_key, "1", timeout=A1_TO_A2_REMINDER_RUN_LOCK_TTL_SECONDS):
        return

    def _runner():
        try:
            _run_due_a1_to_a2_reminders()
        finally:
            cache.delete(run_lock_key)

    threading.Thread(target=_runner, daemon=True).start()


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

    subject_na = "Sobre tu aplicación al Programa de Mentorías"
    html_na = (
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

    subject_ok = "Hemos recibido tu aplicación – Programa de Mentorías"
    html_ok = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Hola querida aplicante :sparkles:</p>"
        "<p>Gracias por completar tu aplicación para recibir mentoría en nuestro Programa de Mentorías.</p>"
        "<p>Tu aplicación ha sido enviada correctamente y no necesitas realizar ninguna acción adicional por ahora.</p>"
        "<p>En la fecha indicada dentro de la aplicación, recibirás un correo electrónico en esta misma dirección únicamente si eres seleccionada, "
        "con los siguientes pasos a seguir.</p>"
        "<p>Te recomendamos estar pendiente de tu correo, incluyendo la bandeja de spam o promociones, para no perder esta información importante.</p>"
        "<p>Si eres seleccionada, deberás completar dentro de la fecha límite que se te indicará:</p>"
        "<ul>"
        "<li>:heavy_check_mark: Firmar el Acta de Compromiso</li>"
        "<li>:heavy_check_mark: Completar la capacitación</li>"
        "</ul>"
        "<p>Estos pasos son necesarios para poder asignarte una mentora y participar en la reunión de lanzamiento e inicio de mentorías.</p>"
        "<p>Gracias por tu interés en ser parte de esta comunidad :heartpulse:</p>"
        "<p>Con gratitud,<br><strong>Equipo de Club Emprendo</strong></p>"
        "</div>"
    )

    try:
        _send_html_email(
            to_email,
            subject_na if send_disqualified_email else subject_ok,
            html_na if send_disqualified_email else html_ok,
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

        form2_url = request.build_absolute_uri(
            reverse("apply_mentora_second", kwargs={"token": app.invite_token})
        )

        deadline_str = ""
        grp = getattr(app.form, "group", None)
        if grp and getattr(grp, "a2_deadline", None):
            deadline_str = grp.a2_deadline.strftime("%d/%m/%Y")

        subject = "Siguiente paso: Completa la segunda solicitud"
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
            "<p><strong>Querida aplicante a Mentora,</strong></p>"
            "<p>Gracias por completar la primera aplicación para ser mentora en Club Emprendo. 🌱</p>"
            "<p>Con base en tus respuestas, confirmamos que cumples con los requisitos y la disponibilidad necesaria, por lo que estás habilitada para continuar con el proceso.</p>"
            "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el segundo y último paso para postularte como mentora voluntaria.</p>"
            "<p><strong>📌 Instrucciones para acceder a la Aplicación #2:</strong></p>"
            "<ol>"
            f'<li>Haz clic aquí: 👉 <a href="{form2_url}">Aplicación 2</a>'
            f'{" — Fecha límite: " + deadline_str if deadline_str else ""}</li>'
            "<li>Lee con atención y responde cada pregunta.</li>"
            "</ol>"
            "<p>Gracias nuevamente por tu interés y compromiso con otras mujeres emprendedoras 💛</p>"
            "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
            "</div>"
        )
        _send_html_email(app.email, subject, html_body)
        return

    app.invited_to_second_stage = False
    app.save(update_fields=["invited_to_second_stage"])

    subject = "Sobre tu aplicación como mentora voluntaria 🌟"
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida aplicante a mentora,</p>"
        "<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. 💛</p>"
        "<p>En la aplicación indicaste que no cumples uno o más requisitos o disponibilidad para esta cohorte, por eso no podremos enviarte el paso 2.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
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


def _sections_from_model(form_def: FormDefinition, form):
    """
    Build a list of section dictionaries using Section model assignments.
    """
    sections_qs = list(
        form_def.sections.select_related("show_if_question", "show_if_question_2").order_by("position", "id")
    )
    if not sections_qs:
        return None

    q_by_id = {q.id: q for q in form_def.questions.all()}

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
        "intro": "",
        "fields": [],
    }

    for field in form:
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


def _combined_display_name_from_slug(slug: str) -> str | None:
    s = slug or ""
    if s.endswith("E_A1") or s.endswith("E_A2"):
        return "Formulario de Emprendedoras"
    if s.endswith("M_A1") or s.endswith("M_A2"):
        return "Formulario de Mentoras"
    return None


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
        candidate_suffix = "_E_A2"
    else:
        form_slug = "M_A2"
        candidate_suffix = "_M_A2"

    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}{candidate_suffix}"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate
    return form_slug


def _handle_application_form(
    request,
    form_slug: str,
    second_stage: bool = False,
    combined_flow: bool = False,
    invite_token: str | None = None,
):
    _maybe_run_due_a1_to_a2_reminders()
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
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
    if not is_admin_preview and not form_def.is_public:
        return render(
            request,
            "applications/closed.html",
            {"form_def": form_def},
            status=403,
        )


    if request.method == "POST" and not getattr(form_def, "accepting_responses", True):
        return render(
            request,
            "applications/form_closed.html",
            {
                "form_def": form_def,
                "second_stage": second_stage,
            },
            status=403,
        )

    ApplicationForm = build_application_form(form_slug)

    rendered_description = ""
    for attr in ("description", "intro", "intro_text", "public_description"):
        if hasattr(form_def, attr):
            v = getattr(form_def, attr) or ""
            if str(v).strip():
                rendered_description = str(v)
                break

    if rendered_description.strip() and (
        rendered_description.strip() == (form_def.description or "").strip()
    ):
        rendered_description = ""

    if request.method == "POST":
        form = ApplicationForm(request.POST)
    else:
        form = ApplicationForm()

    _apply_question_conditions(form)

    sections = _sections_from_model(form_def, form)
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

        reuse_token = (invite_token or request.GET.get("token") or "").strip()
        existing_app = None
        if reuse_token and (form_def.slug.endswith("E_A2") or form_def.slug.endswith("M_A2")):
            existing_app = (
                Application.objects.select_related("form")
                .filter(invite_token=reuse_token)
                .first()
            )
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
            for q in form_def.questions.filter(active=True).order_by("position", "id"):
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

        try:
            gnum_raw = _group_num_from_slug(form_def.slug or "")
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

            if passed:
                app.generate_invite_token()
                app.invited_to_second_stage = True
                app.save(update_fields=["invite_token", "invited_to_second_stage"])
                app.refresh_from_db()
                _schedule_a1_to_a2_reminder(app)
                continue_url = f"{request.path}?combined=1&token={app.invite_token}"
                return redirect(continue_url)

            app.invited_to_second_stage = False
            app.save(update_fields=["invited_to_second_stage"])
            app.refresh_from_db()
            _schedule_a1_to_a2_reminder(app)
            request.session["ce_thanks_payload"] = {
                "kind": "a1",
                "approved": False,
                "group_num": _group_num_from_slug(form_def.slug or ""),
                "track": _a1_track_from_slug(form_def.slug or ""),
            }
            return redirect("application_thanks")

        # A1 autogrades
        if form_def.slug.endswith("M_A1"):
            _mentor_a1_autograde_and_email(request, app)
            app.refresh_from_db()
            _schedule_a1_to_a2_reminder(app)

        if form_def.slug.endswith("E_A1"):
            autograde_and_email_emprendedora_a1(request, app)
            app.refresh_from_db()
            _schedule_a1_to_a2_reminder(app)

        # group number (from slug like G5_M_A1)
        group_num = _group_num_from_slug(form_def.slug or "")

        # Track for rejection message
        track = _a1_track_from_slug(form_def.slug or "")

        # ✅ Thank-you routing
        if form_def.slug.endswith("M_A2"):
            request.session["ce_thanks_payload"] = {
                "kind": "mentor_final",
                "group_num": group_num,
                "disqualified": a2_disqualified,
            }
        elif form_def.slug.endswith("E_A2"):
            request.session["ce_thanks_payload"] = {
                "kind": "emprendedora_final",
                "group_num": group_num,
                "disqualified": a2_disqualified,
            }
        else:
            request.session["ce_thanks_payload"] = {
                "kind": "a1",
                "approved": bool(app.invited_to_second_stage),
                "group_num": group_num,
                "track": track,
            }

        return redirect("application_thanks")

    return render(
        request,
        "applications/application_form.html",
        {
            "form": form,
            "form_def": form_def,
            "second_stage": second_stage,
            "combined_flow": combined_flow,
            "display_form_name": _combined_display_name_from_slug(form_def.slug) if combined_flow else None,
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

    latest = _latest_group_form_slug("E_A1")
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

    latest = _latest_group_form_slug("M_A1")
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

    latest = _latest_group_form_slug("E_A1", combined_only=True)
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

    latest = _latest_group_form_slug("M_A1", combined_only=True)
    return _handle_application_form(
        request,
        latest or "M_A1",
        second_stage=False,
        combined_flow=True,
    )


def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)

    form_slug = "E_A2"
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_E_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

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

    form_slug = "M_A2"
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_M_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

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
