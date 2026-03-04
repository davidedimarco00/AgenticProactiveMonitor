# 3. DevOps and Development Process

This section describes the DevOps practices, workflows, and automation strategies adopted during the development of the SmartGym Monitor system.

## 3.1 Version Control Strategy

The project follows a **Git Flow** branching model to ensure a structured and controlled development process.

The main branches are:

- `main`: contains only stable and production-ready code.
- `develop`: integration branch where completed features are merged before release.
- `feature/<feature-name>`: used for the development of individual features.

Each new functionality is developed in a dedicated `feature` branch created from `develop`.

Each `feature` branch is in turn broken down into sub-branches `feature/<feature-name>-<sub-name>` that make up the feature in its entirety.

Once completed, the feature is merged back into `develop` through a Pull Request.

The `main` branch only receives code that has been validated and is ready for release.

This approach ensures:

- Isolation of new features
- Controlled integration
- Clear separation between stable and in-progress code

## 3.2 Branch Protection Rules

To preserve repository integrity and prevent accidental changes to production code, strict protection rules are applied to the `main` branch,
using GitHub rulesets.

The following restrictions are enforced:

- Direct pushes to `main` are prohibited.
- Code cannot be added or deleted directly on `main`.
- Every Pull Request targeting `main` requires at least one review before merging.
- All required CI checks must pass before merge approval (e.g., commit message validation).

This guarantees that production code is always reviewed, tested, and validated.

## 3.3 Conventional Commits and Branch Naming

The project adopts the **Conventional Commits specification** to standardize commit messages and improve traceability.
More info on Conventional Commits can be found [here](https://www.conventionalcommits.org/en/v1.0.0/).

Commit messages follow a structured format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Examples:

- `feat(auth): add login endpoint`
- `fix(api): resolve session validation bug`
- `docs(readme): update setup instructions`

Branch naming also follows a consistent convention, the **Conventional Branch specification**.
More info on Conventional Branch Naming can be found [here](https://conventional-branch.github.io/).

Branch names are structured as follows:

```
<type>/<description>
```

Examples:

- `feature/<feature-name>`
- `fix/<bug-name>`
- `feature/docs`

### Automated Enforcement

To enforce these conventions:

- **Husky** is used to configure Git hooks locally.
  - A _commit-msg_ hook validates commit messages before they are accepted.
  - A _pre-commit_ hook formats code to ensure code style consistency.
- A **GitHub Action** runs on each Pull Request to verify that all commits comply with the Conventional Commit standard.

This prevents inconsistent commit history and improves maintainability.

## 3.4 Continuous Integration (CI)

A Continuous Integration pipeline is configured using GitHub Actions.

The CI workflow is triggered on:

- Pull Requests
- Pushes to `develop` and `main`

The pipeline includes:

- Automated testing
- Validation of commit conventions
- Build verification

This ensures that:

- The project always builds successfully
- Tests are executed automatically
- Errors are detected early in the development lifecycle

## 3.5 Continuous Deployment (CD)

The project includes automated deployment workflows.

### Documentation Deployment

The system documentation is built using **VitePress**.

A dedicated GitHub Action:

- Builds the documentation site
- Automatically deploys it (e.g., via GitHub Pages)

This ensures that the documentation is always aligned with the latest version of the project.

### Automated Releases

When code is merged into `main`, a release workflow is triggered.

- TO DO: describe the release process, e.g., using semantic versioning, generating release notes, etc.

## 3.6 Automated Testing

Automated tests are integrated into the CI pipeline.

This prevents regressions and increases confidence in code quality.

Testing automation guarantees:

- Early bug detection
- Stable integration into `develop`
- High reliability of releases to `main`

## 3.7 Dependency Management with Renovate

The repository integrates **Renovate Bot** to automate dependency updates.

Renovate:

- Monitors project dependencies
- Automatically creates Pull Requests when updates are available
- Ensures dependencies remain up to date

Each update is reviewed and validated through the CI pipeline before being merged.

## 3.8 DevOps Benefits

The adopted DevOps strategy provides:

- Structured collaboration
- High code quality
- Automated validation
- Safe and controlled releases
- Continuous documentation updates
- Automated dependency management

Overall, the combination of Git Flow, CI/CD automation, commit standardization, and dependency monitoring significantly increases
the robustness and maintainability of the SmartGym Monitor system.
