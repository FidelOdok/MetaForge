# design_cad_iterative

Refine one component's CAD in a self-validating loop through
`twin.design_cad_loop`: a model writes CadQuery from the component's
parametric plan, the script is built and checked in the CadQuery adapter
(validity, measurements, DFM for the selected process), rendered from fixed
views in a headless browser and reviewed by a vision critic against the
intent and the manufacturing context; the model revises until done.

## When to use it

- A component has a parametric plan (a `PSK` item) and the person wants a
  better model without editing CadQuery by hand.
- After a DFM finding or a review comment that a few rounds of edits can fix.

Not for a component with no parametric plan (make it editable first), and
not to accept anything: the result is a proposal.

## Stop rules

It stops `satisfied` only when the critic is satisfied, validity passes, no
blocking DFM finding remains and stated overall sizes are within tolerance.
Otherwise it stops at `max_iterations` (default 5) or the run's model call and
spend budget with its open issues, `needs_input` when a dimension is unknown
(it never invents one; with `allow_working_values` it may use a labelled
working value, shown to the person), or `failed` when a model or adapter
cannot be reached.

## Output

A job (or, with `wait`, the run): the record's node id, the stop reason, the
questions and the proposed version with its open issues. A person accepts it
or keeps the previous version; acceptance builds it through the exact CAD
path, and the critic's verdict never validates anything.
