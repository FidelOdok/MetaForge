# Vendored data: bd_warehouse

These CSV files are copied unmodified from bd_warehouse
(https://github.com/gumyr/bd_warehouse), commit
`8e9be0645b53db3d7414f74828c2d67b0da21138`, directory `src/bd_warehouse/data/`,
under the Apache License 2.0 (`LICENSE` in this directory).

| File | Used for |
|---|---|
| `socket_head_cap_parameters.csv` | ISO 4762 socket head cap screw dimensions |
| `hex_nut_parameters.csv` | ISO 4032 hex nut dimensions |
| `plain_washer_parameters.csv` | ISO 7089 plain washer dimensions |
| `single_row_deep_groove_ball_bearing_parameters.csv` | Deep groove ball bearing dimensions, load ratings, speeds and mass (columns prefixed `SKT:`; the original catalogue is not named in the file) |

MetaForge reads them in `twin_core/library/standard_parts.py` to build
standard-part library entries (FORGE-449). Every value an entry takes from
them cites the file and its row, so the source stays visible on the value.
