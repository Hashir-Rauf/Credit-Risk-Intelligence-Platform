# Final report (LaTeX)

`main.tex` is the final project report: metrics, figures and screenshots, written in LaTeX. Figures are included straight from
`docs/evidence/`, `docs/diagrams/` and `reports/plots/`, so nothing is duplicated and the report stays in step with the evidence.

## Build
Needs a TeX distribution (MiKTeX or TeX Live) with `pdflatex` and `latexmk`.
```bash
cd report
latexmk -pdf -interaction=nonstopmode main.tex
```
To keep the build files out of the repository, send them elsewhere:
```bash
latexmk -pdf -interaction=nonstopmode -outdir=/path/to/build main.tex
```
The built PDF (`credit-risk-final-report.pdf`, about 3.5 MB because of the full-resolution screenshots) is git-ignored; attach it to the
submission instead of committing it.

## Before submitting
* Section 14 (individual contributions) was written from the Git history: every member must confirm or amend it.
* Section 1 lists what has **not** been demonstrated yet (AWS rollback run, blocked deployment, alarm firing on AWS, cost view, teardown,
  reproduction by someone else). Update that table and Section 7 when those are done.
