MANUAL_SOURCE = "manual"
MANUAL_SOURCE_DETAIL = "manual-control-center"


def lead_source_category(source: str, source_detail: str | None = None) -> str:
    if source == MANUAL_SOURCE or source_detail == MANUAL_SOURCE_DETAIL:
        return MANUAL_SOURCE
    return source
