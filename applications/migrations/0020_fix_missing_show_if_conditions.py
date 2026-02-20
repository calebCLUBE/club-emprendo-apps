from django.db import migrations


def ensure_show_if_conditions(apps, schema_editor):
    """
    Safety net: add applications_section.show_if_conditions if it was dropped
    (seen in production) even though migrations are marked applied.
    """
    conn = schema_editor.connection
    vendor = conn.vendor
    cursor = conn.cursor()

    if vendor == "postgresql":
        cursor.execute(
            """
            ALTER TABLE applications_section
            ADD COLUMN IF NOT EXISTS show_if_conditions JSONB NOT NULL DEFAULT '[]'::jsonb;
            """
        )
        return

    if vendor == "sqlite":
        # Check existing columns first
        cols = [row[1] for row in cursor.execute("PRAGMA table_info(applications_section);")]
        if "show_if_conditions" in cols:
            return
        cursor.execute(
            "ALTER TABLE applications_section ADD COLUMN show_if_conditions TEXT NOT NULL DEFAULT '[]';"
        )
        return

    # Fallback for other vendors: try a generic add-if-missing
    try:
        cursor.execute(
            """
            ALTER TABLE applications_section
            ADD COLUMN show_if_conditions JSON NOT NULL DEFAULT '[]';
            """
        )
    except Exception:
        # If the column already exists or the DB doesn't support JSON, ignore.
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0019_merge_question_and_section_conditions"),
    ]

    operations = [
        migrations.RunPython(ensure_show_if_conditions, migrations.RunPython.noop, elidable=True),
    ]
