# Contributing

A figure that differs from the official source, or a missing municipality:
open an issue with the "Figure differs from the source" template. To send
code:

- Tests run from the checkout, with nothing to install and no network:

  ```bash
  python -m unittest discover -s tests -t .
  ```

- Standard library only: no dependencies.
- Every figure is read from the official source (SICONFI, SIOPS) as
  published, never recomputed, with the collection date. Absence is
  recorded as absence, never as zero.
- Every new case has a test, with the expected result written by hand in
  the test itself. Tests never touch the network: the HTTP transport and the
  clock are injected.
- No personal data (taxpayer IDs, addresses, contacts) in code, tests or
  issues. Municipality names and figures, as the official source publishes
  them, are fine.
- Documentation and commit messages in English; identifiers follow the
  existing code.
- Behavior changes are described in the pull request.
- Your own code only: do not bring code copied from another project, even
  under a free license. What you contribute is released under the project's
  [AGPL-3.0-or-later license](LICENSE).
