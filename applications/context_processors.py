from django.conf import settings


def google_ads_tracking(request):
    """Expose public Google Ads tag identifiers to public templates."""
    return {
        "google_ads_conversion_id": getattr(
            settings,
            "GOOGLE_ADS_CONVERSION_ID",
            "",
        ),
        "google_ads_conversion_label": getattr(
            settings,
            "GOOGLE_ADS_CONVERSION_LABEL",
            "",
        ),
    }
