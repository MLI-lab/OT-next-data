---
name: plot-results
description: Chart pass@k and other run results so the numbers stay honest and readable.
---

# Plotting results

`verify/plot_pass_rates.py` is the reference implementation:

```bash
python verify/plot_pass_rates.py "Model A=<run dir>" "Model B=<dir>,<dir>" -o pass_rates.png
python verify/plot_pass_rates.py ... --theme dark -o pass_rates-dark.png
```

It reads the same `validated_attempt_summary.json` files as `verify/pass_at_k.py`,
so a chart can never disagree with the reported table. Charts are regenerated
from run data, never typed in by hand.

## Conventions

- **One panel per model**, shared x-scale, so bar lengths compare across models.
  A model with one attempt per task shows pass@1 only, and its subtitle says
  "1 attempt per task" so the missing bars do not read as missing data.
- **Horizontal bars per language**, with the aggregate row separated by a gap and
  set in semibold.
- **pass@1 / 4 / 16 as one blue ramp, light to dark** — an ordinal ramp, because k
  is ordered. Never three unrelated hues: the ordering must survive greyscale and
  colour-vision deficiency.
- Value at each bar tip in muted ink, hairline gridlines, no chart border, and
  never the series colour on text.
- Light and dark variants are separate renders against their own surface colour,
  not an inverted image.

## Honesty rules

- State the attempt count and the timeout rate next to any pass@k number:
  timeouts count as failures, so an infrastructure problem looks like a weak
  model.
- Report the strict subset beside the full set when one exists.
- Bars start at zero, and two measures of different scale never share a chart on
  two y-axes — use two charts.
