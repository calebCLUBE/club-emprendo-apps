from django.core.management.base import BaseCommand, CommandError

from applications.admin_views import (
    RECRUITMENT_POOL_SOURCES,
    _ensure_recruitment_pool_group,
)


class Command(BaseCommand):
    help = "Restore recruitment pool group(s) and required group forms."

    def add_arguments(self, parser):
        parser.add_argument(
            "--pool",
            default="april_recruitment",
            help="Pool key to restore (default: april_recruitment).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Restore all configured recruitment pools.",
        )

    def handle(self, *args, **options):
        restore_all = bool(options.get("all"))
        selected_pool = str(options.get("pool") or "").strip()

        if restore_all:
            pool_keys = list(RECRUITMENT_POOL_SOURCES.keys())
        else:
            if selected_pool not in RECRUITMENT_POOL_SOURCES:
                valid = ", ".join(sorted(RECRUITMENT_POOL_SOURCES.keys()))
                raise CommandError(f"Unknown pool '{selected_pool}'. Valid: {valid}")
            pool_keys = [selected_pool]

        for pool_key in pool_keys:
            summary = _ensure_recruitment_pool_group(pool_key)
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        f"{summary['label']} restored "
                        f"(group {summary['group_num']}) | "
                        f"group_created={summary.get('group_created', False)} "
                        f"forms_created={summary.get('forms_created', 0)} "
                        f"forms_relinked={summary.get('forms_relinked', 0)}"
                    )
                )
            )
