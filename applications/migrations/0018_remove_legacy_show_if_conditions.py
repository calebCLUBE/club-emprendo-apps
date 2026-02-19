from django.db import migrations


def _column_exists_sqlite(cursor):
    return [row[1] for row in cursor.execute("PRAGMA table_info(applications_section);")]


def drop_show_if_conditions(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    vendor = schema_editor.connection.vendor

    if vendor == "sqlite":
        cols = _column_exists_sqlite(cursor)
        if "show_if_conditions" not in cols:
            return
        cursor.execute("ALTER TABLE applications_section DROP COLUMN show_if_conditions;")
        return

    if vendor == "postgresql":
        cursor.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'applications_section'
                      AND column_name = 'show_if_conditions'
                ) THEN
                    ALTER TABLE applications_section DROP COLUMN show_if_conditions;
                END IF;
            END$$;
            """
        )
        return

    cursor.execute("ALTER TABLE applications_section DROP COLUMN show_if_conditions")


def add_show_if_conditions(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    vendor = schema_editor.connection.vendor

    if vendor == "sqlite":
        cols = _column_exists_sqlite(cursor)
        if "show_if_conditions" in cols:
            return
        cursor.execute(
            "ALTER TABLE applications_section ADD COLUMN show_if_conditions TEXT NOT NULL DEFAULT ''"
        )
        return

    if vendor == "postgresql":
        cursor.execute(
            """
            ALTER TABLE applications_section
            ADD COLUMN IF NOT EXISTS show_if_conditions TEXT NOT NULL DEFAULT '';
            """
        )
        return

    cursor.execute(
        "ALTER TABLE applications_section ADD COLUMN show_if_conditions TEXT NOT NULL DEFAULT ''"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("applications", "0017_section_multiple_conditions"),
    ]

    operations = [
        migrations.RunPython(drop_show_if_conditions, add_show_if_conditions, elidable=True),
    ]
