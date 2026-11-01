# Publish as a Hermes plugin

This folder is a standalone native Hermes plugin project. Publish its contents
as the root of your plugin repository. There is no `stage1` directory in this
checkout; that name applies only if you later embed the plugin under a
subdirectory of another repository.

## Validate and distribute

```bash
python3 -m unittest discover -s tests -v
hermes plugins validate .
hermes plugins doctor . --ci
```

Push the source to your chosen repository. Users can then run:

```bash
hermes plugins install rewanthtammana/hermes-flow --enable
hermes flow --open
```

No PyPI upload is required for Git-based Hermes installation. Do not publish
run captures, private session metadata, virtual environments, or bytecode. The
example report in this project is synthetic and labeled accordingly.

Before tagging a release, run the full CI matrix and verify a clean-profile
installation. Contributors can use `bash scripts/install-local.sh`; it validates
the checkout, handles the dependency-free package-manager fallback used by some
Hermes container images, and runs the synthetic smoke test.

## Optional curated catalog listing

Hermes's catalog requires a public repository and a maintainer-reviewed PR to
`NousResearch/hermes-agent`; it is not a self-service upload. After publishing
the source and choosing the release commit:

1. Run `hermes plugins validate . --install-deps` at that commit.
2. Add `plugin-catalog/hermes-flow.yaml` to a fork of `NousResearch/hermes-agent`.
3. Set its repository, full 40-character commit SHA, maintainer, description,
   quoted version `"0.1.0"`, and `requires_hermes: ">=0.21.6"`. Add `subdir: stage1`
   only when publishing this directory inside the existing repository.
4. Declare no tools, hooks, middleware, or required environment variables. The
   plugin registers one CLI command, `flow`, using `ctx.register_cli_command`.
5. Open the catalog PR with a graph screenshot and the data/execution disclosure
   from the README. Wait for CI and a maintainer review.

Repository URL, release SHA, and maintainer identity must be filled in with real
values before submission. Publishing and catalog acceptance are separate steps.

Official guides are available in the Hermes documentation.
