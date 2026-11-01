# Contributing

Thanks for helping improve Hermes Flow.

## Development

Hermes Flow supports Python 3.10+ and has no runtime Python dependencies.
From the repository root, run:

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s examples/customer_review -p 'test_*.py' -v
python3 -m hermes_flow examples
```

If Hermes is installed, also validate the native plugin:

```bash
hermes plugins validate .
hermes plugins doctor . --ci
```

Keep generated reports, run captures, credentials, and session databases out of
commits. Changes should include tests when behavior changes, and documentation
should explain any user-facing command or output change.

## Pull requests

Describe the user problem, the behavior change, and the verification you ran.
Keep pull requests focused. Do not include private session data or provider
credentials in fixtures; use synthetic data or redacted metadata instead.
