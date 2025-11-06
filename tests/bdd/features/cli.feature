Feature: Lading CLI scaffolding
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

  Scenario: Bumping with an invalid version fails fast
    Given a workspace directory with configuration
    When I invoke lading bump 1.2 with that workspace
    Then the bump command reports an invalid version error for "1.2"

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

  Scenario: Bumping workspace versions when already up to date
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace manifests record version "1.2.3"
    When I invoke lading bump 1.2.3 with that workspace
    Then the bump command reports no manifest changes for "1.2.3"

  Scenario: Running the publish command with a workspace root
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"

  Scenario: Publish command stages workspace README for crates
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace README contains a TOML dependency snippet for "alpha"
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"
    And the publish staging directory for crate "alpha" contains the workspace README
    And the publish plan lists copied workspace README for crate "alpha"

  Scenario: Publish command errors when workspace README is missing
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace README is removed
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Workspace README.md is required by crates that set readme.workspace = true"

  Scenario: Publish command reports skipped crates and missing exclusions
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with publish filtering cases
    And publish.exclude contains "gamma"
    And publish.exclude contains "missing-delta"
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"
    And the publish command reports manifest-skipped crate "beta"
    And the publish command reports configuration-skipped crate "gamma"
    And the publish command reports missing exclusion "missing-delta"

  Scenario: Publish command lists multiple configuration skipped crates
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with publish filtering cases
    And publish.exclude contains "gamma"
    And publish.exclude contains "delta"
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"
    And the publish command reports configuration-skipped crates "gamma, delta"
    And the publish command omits section "Configured exclusions not found in workspace:"

  Scenario: Publish command reports no publishable crates
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with no publishable crates
    When I invoke lading publish with that workspace
    Then the publish command reports that no crates are publishable
    And the publish command reports manifest-skipped crate "alpha"
    And the publish command reports manifest-skipped crate "beta"

  Scenario: Publish command orders crates by dependency
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    When I invoke lading publish with that workspace
    Then the publish command lists crates in order "alpha, beta, gamma"

  Scenario: Publish command honours configured order
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    And publish.order is "gamma, beta, alpha"
    When I invoke lading publish with that workspace
    Then the publish command lists crates in order "gamma, beta, alpha"

  Scenario: Publish command ignores dev dependency cycles
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a dev dependency cycle
    When I invoke lading publish with that workspace
    Then the publish command lists crates in order "alpha, beta"

  Scenario: Publish command rejects duplicate publish order entries
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    And publish.order is "alpha, alpha"
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Duplicate publish.order entries: alpha"

  Scenario: Publish command reports dependency cycles
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency cycle
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Cannot determine publish order due to dependency cycle involving: alpha, beta"

  Scenario: Publish command aborts when cargo check fails
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And cargo check fails during publish pre-flight
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Pre-flight cargo check failed with exit code 1: cargo check failed"

  Scenario: Publish command aborts when cargo test fails
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And cargo test fails during publish pre-flight
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Pre-flight cargo test failed with exit code 1: cargo test failed"

  Scenario: Publish pre-flight skips configured cargo test crates
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.test_exclude contains "alpha"
    When I invoke lading publish with that workspace
    Then the publish command excludes crate "alpha" from pre-flight tests

  Scenario: Publish pre-flight limits cargo test targets to libraries and binaries
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.unit_tests_only is true
    When I invoke lading publish with that workspace
    Then the publish command limits pre-flight tests to libraries and binaries

  Scenario: Publish pre-flight aborts when cmd-mox socket is missing
    Given a workspace directory with configuration
    And cmd-mox IPC socket is unset
    When I run publish pre-flight checks for that workspace
    Then the publish pre-flight error contains "cmd-mox stub requested for publish pre-flight but CMOX_IPC_SOCKET is unset"

  Scenario: Publish command rejects dirty workspaces without allow-dirty
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has uncommitted changes
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Workspace has uncommitted changes; commit or stash them before publishing or re-run with --allow-dirty."

  Scenario: Publish command allows dirty workspaces with allow-dirty flag
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has uncommitted changes
    When I invoke lading publish with that workspace using --allow-dirty
    Then the publish command prints the publish plan for "alpha"

  Scenario: Running the bump command without configuration
    Given a workspace directory without configuration
    When I invoke lading bump 1.2.3 with that workspace
    Then the CLI reports a missing configuration error
