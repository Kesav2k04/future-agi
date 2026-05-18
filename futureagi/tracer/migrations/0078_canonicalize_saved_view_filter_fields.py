from django.db import migrations


FILTER_CONFIG_KEYS = (
    "filters",
    "compareFilters",
    "extraFilters",
    "compareExtraFilters",
)


FILTER_ITEM_KEY_ALIASES = {
    "columnId": "column_id",
    "displayName": "display_name",
    "outputType": "output_type",
}


FILTER_CONFIG_KEY_ALIASES = {
    "filterType": "filter_type",
    "filterOp": "filter_op",
    "filterValue": "filter_value",
    "colType": "col_type",
}


def _canonicalize_filter_item(filter_item):
    if not isinstance(filter_item, dict):
        return False

    changed = False
    for old_key, new_key in FILTER_ITEM_KEY_ALIASES.items():
        if old_key in filter_item and new_key not in filter_item:
            filter_item[new_key] = filter_item.pop(old_key)
            changed = True

    config = filter_item.get("filter_config")
    if not isinstance(config, dict) and isinstance(filter_item.get("filterConfig"), dict):
        filter_item["filter_config"] = filter_item.pop("filterConfig")
        config = filter_item["filter_config"]
        changed = True

    if isinstance(config, dict):
        for old_key, new_key in FILTER_CONFIG_KEY_ALIASES.items():
            if old_key in config and new_key not in config:
                config[new_key] = config.pop(old_key)
                changed = True

    return changed


def canonicalize_saved_view_filter_fields(apps, schema_editor):
    SavedView = apps.get_model("tracer", "SavedView")
    for saved_view in SavedView.objects.iterator(chunk_size=500):
        config = saved_view.config
        if not isinstance(config, dict):
            continue

        changed = False
        for key in FILTER_CONFIG_KEYS:
            filters = config.get(key)
            if not isinstance(filters, list):
                continue
            for filter_item in filters:
                changed = _canonicalize_filter_item(filter_item) or changed

        if changed:
            saved_view.save(update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0077_merge_20260514_1559"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_saved_view_filter_fields,
            migrations.RunPython.noop,
        ),
    ]
