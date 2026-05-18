from __future__ import annotations


CONTRACT_ENDPOINTS = {
    "courses_index": "/video-courses?limit=100",
    "tutorials_index": "/tutorials?limit=100",
    "operators": "/operators",
    "data_categories": "/data-categories",
    "data_fields": "/data-fields?instrumentType=EQUITY&region=USA&delay=1&universe=TOP3000&limit=50&offset=0",
}


def expected_contract_shape() -> dict[str, dict[str, object]]:
    return {
        "courses_index": {
            "type": "dict",
            "required_keys": ["results"],
        },
        "tutorials_index": {
            "type": "dict",
            "required_keys": ["results"],
        },
        "operators": {
            "type": "list",
            "required_item_keys": ["name", "category", "definition"],
        },
        "data_categories": {
            "type": "list",
            "required_item_keys": ["name"],
        },
        "data_fields": {
            "type": "dict",
            "required_keys": ["count", "results"],
            "required_item_keys": ["id"],
        },
    }
