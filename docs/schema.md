# Processed JSON schema

The pipeline emits two files into `data/processed/`. Both are static, served directly to the browser.

## `schools_wa.json`

Object keyed by `nces_id` (12-character NCESSCH string). Each value is a school record:

| Field                          | Type                                       | Notes                                                                                  |
| ------------------------------ | ------------------------------------------ | -------------------------------------------------------------------------------------- |
| `nces_id`                      | string                                     | Same as the parent key. Duplicated for convenience.                                    |
| `name`                         | string                                     | School name from CCD.                                                                  |
| `district`                     | string                                     | LEA name from CCD.                                                                     |
| `city`                         | string                                     | City of the school's address.                                                          |
| `grades`                       | string                                     | Grade range, e.g. `"K-5"`, `"9-12"`.                                                   |
| `address`                      | string                                     | Street address from CCD.                                                               |
| `median_family_income`         | number \| null                             | Estimated median income of zoned families with own children under 18. Null if unknown. |
| `share_under_35k`              | number \| null                             | Share (0–1) of zoned families with income under $35k. Null if total is 0.              |
| `share_over_150k`              | number \| null                             | Share (0–1) of zoned families with income over $150k. Null if total is 0.              |
| `total_families_with_children` | number                                     | Estimated count of zoned families with own children under 18. ≥ 0.                     |
| `bracket_histogram`            | array of `{label, lower, upper, count}`    | See below. Counts are non-negative; `lower < upper`, except `upper: null` for the top open bracket. |
| `low_confidence`               | bool                                       | True if the estimate should be treated with caution.                                   |
| `low_confidence_reasons`       | array of string (see below)                | Empty when `low_confidence` is false.                                                  |

### `bracket_histogram` element

```json
{"label": "$50k-$75k", "lower": 50000, "upper": 75000, "count": 240}
```

The top bracket is open-ended:

```json
{"label": "$200k+", "lower": 200000, "upper": null, "count": 467}
```

Brackets must be contiguous and non-overlapping. The exact bracket cut-points get locked in [pipeline/brackets.py](../pipeline/brackets.py) during Phase 5; the Phase 3 fixture uses an 8-bucket simplification to exercise the schema.

### `low_confidence_reasons` allowed values

| Value                  | Meaning                                                                                  |
| ---------------------- | ---------------------------------------------------------------------------------------- |
| `missing_sabs`         | The NCES SABS attendance polygon for this school is unavailable — no zone to interpolate over. The histogram is all zeros and the income fields are null. |
| `low_household_count`  | The interpolated zone contains fewer than 50 families with children. Estimates are dominated by sampling noise. |

If both conditions trigger, both reasons appear in the array.

## `search_index.json`

Array of slim records, one per school, used to build the client-side fuzzy-search index. Required keys (and nothing else):

| Field      | Type   |
| ---------- | ------ |
| `nces_id`  | string |
| `name`     | string |
| `district` | string |
| `city`     | string |

Every `nces_id` in `search_index.json` must exist as a key in `schools_wa.json`.
