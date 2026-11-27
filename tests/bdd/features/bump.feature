Feature: Lading bump behaviour

  Scenario: Bumping workspace versions updates Cargo manifests
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading bump 1.2.3 with that workspace
    Then the bump command reports manifest updates for "1.2.3"
    And the CLI output lists manifest paths "- Cargo.toml" and "- crates/alpha/Cargo.toml"
    And the workspace manifest version is "1.2.3"
    And the crate "alpha" manifest version is "1.2.3"

  Scenario: Dry running the bump command previews manifest updates
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading bump 1.2.3 with that workspace using --dry-run
    Then the bump command reports a dry-run plan for "1.2.3"
    And the CLI output lists manifest paths "- Cargo.toml" and "- crates/alpha/Cargo.toml"
    And the workspace manifest version is "0.1.0"
    And the crate "alpha" manifest version is "0.1.0"

  Scenario: Bump updates prerelease references in the configured GPUI fixture
    Given a workspace directory with configuration
    And cargo metadata describes the rstest-bdd workspace and published fixture
    When I invoke lading bump 0.6.0 with that workspace
    Then the bump command reports manifest updates for "0.6.0"
    And the CLI output lists manifest path "- tests/fixtures/published-gpui-e2e/Cargo.toml"
    And the published fixture manifest references only 0.6.0

  Scenario: Dry-run previews prerelease rewrites in the configured GPUI fixture
    Given a workspace directory with configuration
    And cargo metadata describes the rstest-bdd workspace and published fixture
    And the workspace has tracked Cargo.lock files
    When I invoke lading bump 0.6.0 with that workspace using --dry-run
    Then the bump command reports a dry-run plan for "0.6.0"
    And the CLI output lists manifest path "- tests/fixtures/published-gpui-e2e/Cargo.toml"
    And the published fixture manifest remains unchanged
    And the bump made no Cargo lockfile update invocations

  Scenario: Bump refreshes tracked Cargo.lock files
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has tracked Cargo.lock files
    When I invoke lading bump 1.2.3 with that workspace
    Then the CLI output lists lockfile path "- Cargo.lock (lockfile)"
    And the bump command refreshed tracked lockfiles

  Scenario: Bump rebuilds configured nested lockfiles
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has tracked Cargo.lock files
    And a nested lockfile manifest is configured at "nested/Cargo.toml"
    When I invoke lading bump 1.2.3 with that workspace
    Then the CLI output lists lockfile path "- Cargo.lock (lockfile)"
    And the CLI output lists lockfile path "- nested/Cargo.lock (lockfile)"
    And the bump command refreshed root and configured nested tracked lockfiles

  Scenario: Bump refreshes a discovered nested Cargo.lock file
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has a tracked nested Cargo.lock file
    When I invoke lading bump 1.2.3 with that workspace
    Then the CLI output lists lockfile path "- Cargo.lock (lockfile)"
    And the CLI output lists lockfile path "- fixtures/minimal/Cargo.lock (lockfile)"
    And the bump command refreshed workspace and nested tracked lockfiles

  Scenario: Bump dry-run does not modify lockfiles
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has tracked Cargo.lock files
    When I invoke lading bump 1.2.3 with that workspace using --dry-run
    Then the CLI output lists lockfile path "- Cargo.lock (lockfile)"
    And the bump command did not refresh tracked lockfiles

  Scenario: Bumping with an invalid version fails fast
    Given a workspace directory with configuration
    When I invoke lading bump 1.2 with that workspace
    Then the bump command reports an invalid version error for "1.2"

  Scenario: Bumping labels errors from configured manifest rewrites
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And bump.manifest_rewrites selects an unmatched fixture glob
    When I invoke lading bump 1.2.3 with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Manifest rewrite error: Manifest rewrite glob matched no files"

  Scenario: Bumping workspace versions skips excluded crates
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with crates alpha and beta
    And bump.exclude contains "alpha"
    When I invoke lading bump 1.2.3 with that workspace
    Then the crate "alpha" manifest version is "0.1.0"
    And the crate "beta" manifest version is "1.2.3"

  Scenario: Bumping updates internal dependency requirements
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with internal dependency requirements
    When I invoke lading bump 1.2.3 with that workspace
    Then the dependency "beta:alpha@dependencies" has requirement "^1.2.3"
    And the dependency "beta:alpha@dev-dependencies" has requirement "~1.2.3"
    And the dependency "beta:alpha@build-dependencies" has requirement "1.2.3"

  Scenario: Bumping updates documentation TOML fences
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace README contains a TOML dependency snippet for "alpha"
    And bump.documentation.globs contains "README.md"
    When I invoke lading bump 1.2.3 with that workspace
    Then the documentation file "README.md" contains "alpha = \"1.2.3\""
    And the CLI output lists documentation path "- README.md (documentation)"

  Scenario: Bumping transposes workspace README to crates
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading bump 1.2.3 with that workspace
    Then the crate "alpha" README contains "# Workspace README"
    And the CLI output lists README path "- crates/alpha/README.md (readme)"

  Scenario: Bumping rewrites relative links in transposed README
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace README contains a relative link to "docs/v0-6-0-migration-guide.md"
    When I invoke lading bump 1.2.3 with that workspace
    Then the crate "alpha" README contains "../../docs/v0-6-0-migration-guide.md"

  Scenario: Bumping errors when workspace README is missing for opted-in crates
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace README is removed
    When I invoke lading bump 1.2.3 with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Workspace README.md is required by crates that set readme.workspace = true"

  Scenario: Bumping workspace versions when already up to date
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace manifests record version "1.2.3"
    And the crate "alpha" README already matches the workspace README
    When I invoke lading bump 1.2.3 with that workspace
    Then the bump command reports no manifest changes for "1.2.3"

  Scenario: Running the bump command without configuration
    Given a workspace directory without configuration
    And cargo metadata describes a sample workspace
    When I invoke lading bump 1.2.3 with that workspace
    Then the bump command reports manifest updates for "1.2.3"
    And the workspace manifest version is "1.2.3"
    And the crate "alpha" manifest version is "1.2.3"
