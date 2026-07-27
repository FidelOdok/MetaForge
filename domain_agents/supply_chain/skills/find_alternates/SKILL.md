# find_alternates

Find and rank alternate parts for supply-chain risk mitigation, based on compatibility, availability, price, and risk reduction.

## What it does

1. Takes an original MPN, its key specs, and distributor search results as input
2. Filters candidates for electrical/mechanical compatibility with the specs
3. Ranks the compatible alternates by availability, price, and the risk reduction they offer over the original
4. Returns the ranked alternates with the rationale for each

## Tools Required

- `distributor_search` -- fetch candidate parts + stock/pricing from distributors

## Input

- `mpn` -- original manufacturer part number
- `specs` -- key specifications the alternate must satisfy
- `distributor_results` -- candidate parts (with stock/price) to rank

## Output

- ranked list of alternate parts, each with compatibility, availability, price,
  and a risk-reduction score plus rationale

## Limitations

- Only as good as the `specs` supplied — it does not read the datasheet to infer
  compatibility beyond the provided fields
- Availability/pricing reflect the distributor snapshot passed in, not live stock
- Ranks alternates; it does not commit a substitution to the BOM
