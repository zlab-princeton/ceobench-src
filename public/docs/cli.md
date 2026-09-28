# CEOBench CLI Reference

## novamind-operation

Entry point for novamind-operation CLI.

Commands:
    next-week   Advance the simulation by one week (7 days).
                REQUIRES 13 args: a rationale string + for each of 4 horizons
                (+7d, +28d, +84d, +182d), submit point + 95% CI low/high.

Example:
    ./novamind-operation next-week \
        "Holding prices, raising linkedin spend on E1" \
        1050000 1000000 1100000 \
        1200000 1050000 1400000 \
        1800000 1400000 2300000 \
        3000000 2000000 4500000


### Commands

#### `./novamind-operation next-week`

Advance the simulator by one week (7 days) — REQUIRES a rationale string
plus cash predictions at four horizons, each with point estimate + 95% CI bounds.

Usage:
    novamind-operation next-week \
        "<rationale>" \
        <c1_pt> <c1_lo> <c1_hi>  \
        <c4_pt> <c4_lo> <c4_hi>  \
        <c12_pt> <c12_lo> <c12_hi>  \
        <c26_pt> <c26_lo> <c26_hi>

Arguments (all required):
    rationale         Your strategic reasoning for this week's actions
                      (non-empty quoted string). Replaces the old standalone
                      log_rationale tool. Recorded for analysis but does
                      not affect scoring.
    cash_1wk_point    Point estimate of cash 1 week from today (+7 days).
    cash_1wk_lower    95% CI lower bound for the +7-day forecast.
    cash_1wk_upper    95% CI upper bound for the +7-day forecast.
    cash_4wk_point    Point estimate of cash +28 days.
    cash_4wk_lower    95% CI lower bound for +28 days.
    cash_4wk_upper    95% CI upper bound for +28 days.
    cash_12wk_point   Point estimate of cash +84 days.
    cash_12wk_lower   95% CI lower bound for +84 days.
    cash_12wk_upper   95% CI upper bound for +84 days.
    cash_26wk_point   Point estimate of cash 6 months from today (+182 days).
    cash_26wk_lower   95% CI lower bound for +182 days.
    cash_26wk_upper   95% CI upper bound for +182 days.

Constraints (per horizon): ``lower <= point <= upper``. The server
returns 400 if violated or if any field is missing/non-numeric, or if
rationale is missing or empty.

Predictions are recorded at submission time and scored on percent error
`(point - actual) / actual` once actual cash is known. CI bounds are
scored on coverage (does the actual fall inside [lower, upper]?) and
sharpness (interval width / actual). You are evaluated on prediction
accuracy + calibration at each horizon in addition to realized cash.

Calls the API server to step the simulation forward by one week.
Prints the dashboard to stdout, which includes key metrics,
this week's results, and inbox notifications.

**NOTE:** The next_week call may take several minutes at large subscriber
counts. This is normal — just wait for the response.

Exit code 0 on success, 1 on failure (including missing rationale or predictions).

Automatic daily scripts are unsupported. Run scripts explicitly with novamind-operation python.