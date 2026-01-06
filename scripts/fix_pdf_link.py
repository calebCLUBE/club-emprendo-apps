# scripts/fix_pdf_link.py
from applications.models import Question

PDF_URL = "https://drive.google.com/file/d/1MPN5JD6WoAsEnkgyUOtEEzfUtiMytD3s/view?usp=sharing"

HELP_HTML = (
    '📄 <a href="{}" target="_blank" rel="noopener noreferrer">'
    "Abrir PDF – Introducción al programa de mentoría"
    "</a>"
).format(PDF_URL)

# Master A2 question slugs (from your DB output)
TARGETS = [
    ("E_A2", "reviewed_pdf"),
    ("M_A2", "read_pdf"),
]

def set_master_help_text():
    updated = 0
    for form_slug, q_slug in TARGETS:
        qs = Question.objects.filter(form__slug=form_slug, slug=q_slug)
        if not qs.exists():
            print(f"[WARN] Not found: {form_slug}.{q_slug}")
            continue

        for q in qs:
            q.help_text = HELP_HTML
            q.save(update_fields=["help_text"])
            updated += 1
            print(f"[OK] Updated master: {q.form.slug}.{q.slug}")
    return updated

def sync_to_group_clones():
    """
    Copy help_text from the master question (by slug) into all group clones (G*_E_A2 / G*_M_A2)
    """
    synced = 0
    for master_form_slug, q_slug in TARGETS:
        master_q = Question.objects.filter(form__slug=master_form_slug, slug=q_slug).first()
        if not master_q:
            print(f"[WARN] Master missing, cannot sync: {master_form_slug}.{q_slug}")
            continue

        # Push to any group A2 forms that have the same question slug
        # (This covers G6_E_A2, G7_E_A2, etc.)
        clones = Question.objects.filter(
            form__slug__startswith="G",
            form__slug__endswith=master_form_slug,  # E_A2 or M_A2
            slug=q_slug,
        )
        n = clones.update(help_text=master_q.help_text)
        synced += n
        print(f"[OK] Synced {n} group questions for slug={q_slug} into G*_{master_form_slug}")
    return synced

if __name__ == "__main__":
    print("Setting PDF help_text on masters...")
    u = set_master_help_text()
    print(f"Masters updated: {u}")

    print("\nSyncing to group clones...")
    s = sync_to_group_clones()
    print(f"Group questions synced: {s}")

    print("\nDone.")
