
## Prerequeistes

* `json.hpp`

* `iconv` or `nkfs` for script.sh.

#### Configuration

Uses `setting.json` for pattern matching:

```json
[
  {
    "name": "24",
    "player": "your_user_name",
    "pattern": "^\\d+_(\\d{4})_.*\\.kif$",
    "output_path": "/path/to/output/24_games"
  },
  {
    "name": "wars",
    "player": "your_user_name", 
    "pattern": "^.*?(\\d{8})_.*?\\.kif$",
    "output_path": "/path/to/output/wars_games"
  }
]
```

