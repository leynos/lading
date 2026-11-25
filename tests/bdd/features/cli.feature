Feature: Lading CLI scaffolding

  Scenario: Running the publish command with a workspace root
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"

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

  Scenario: Publish command packages crates in publish order
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    When I invoke lading publish with that workspace
    Then the publish command packages crates in order "alpha, beta, gamma"

  Scenario: Publish command executes cargo publish in dry-run mode
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    When I invoke lading publish with that workspace
    Then the publish command performs cargo publish dry-run for crates "alpha, beta, gamma"

  Scenario: Publish command supports live publishing
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    When I invoke lading publish with that workspace using --live
    Then the publish command performs live cargo publish for crates "alpha, beta, gamma"
    And the publish command interleaves live package and publish for crates "alpha, beta, gamma"

  Scenario: Publish command continues past already published crate versions
    Given a workspace directory with configuration
    And cargo metadata describes a workspace with a publish dependency chain
    And cargo publish reports crate "alpha" already uploaded
    When I invoke lading publish with that workspace
    Then the publish command performs cargo publish dry-run for crates "alpha, beta, gamma"

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

  Scenario: Publish pre-flight aborts when lockfile is stale
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And publish pre-flight finds a stale tracked Cargo.lock
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Tracked Cargo.lock files are stale after manifest version changes."

  Scenario: Publish keeps the lockfile probe's metadata document out of stdout
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And publish pre-flight probes a fresh tracked Cargo.lock with a large metadata document
    When I invoke lading publish with that workspace
    Then the CLI exits with code 0
    And the stdout does not contain "LADING-METADATA-SENTINEL"

  Scenario: Publish pre-flight error includes repair command
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And publish pre-flight finds a stale tracked Cargo.lock
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "cargo generate-lockfile --manifest-path"

  Scenario: Publish pre-flight aggregates every stale Cargo.lock file
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And publish pre-flight finds multiple stale tracked Cargo.lock files
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Tracked Cargo.lock files are stale after manifest version changes."
    And the stderr contains workspace path "Cargo.lock"
    And the stderr contains workspace path "sub/Cargo.lock"
    And the stderr contains "cargo generate-lockfile --manifest-path"
    And the stderr contains workspace path "Cargo.toml"
    And the stderr contains workspace path "sub/Cargo.toml"

  Scenario: Publish pre-flight skips configured cargo test crates
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.test_exclude contains "alpha"
    When I invoke lading publish with that workspace
    Then the publish command excludes crate "alpha" from pre-flight tests

  Scenario: Publish pre-flight ignores blank test excludes
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.test_exclude contains blank entries
    When I invoke lading publish with that workspace
    Then the publish command does not add pre-flight excludes

  Scenario: Publish pre-flight limits cargo test targets to libraries and binaries
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.unit_tests_only is true
    When I invoke lading publish with that workspace
    Then the publish command limits pre-flight tests to libraries and binaries

  Scenario: Auxiliary build commands run before publish pre-flight
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.aux_build contains command "cargo fmt"
    And the preflight command "cargo fmt" exits with code 0 and stderr ""
    When I invoke lading publish with that workspace
    Then the publish command runs auxiliary build "cargo::fmt"

  Scenario: Cargo test pre-flight forwards configured environment variables
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.env sets "DYLINT_LOCALE" to "cy"
    When I invoke lading publish with that workspace
    Then the cargo test pre-flight env contains "DYLINT_LOCALE"="cy"

  Scenario: Cargo test pre-flight injects compiletest extern flags
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.compiletest_extern maps "lint_macro" to "target/liblint_macro.so"
    When I invoke lading publish with that workspace
    Then the cargo test pre-flight env includes "--extern lint_macro" in RUSTFLAGS

  Scenario: Publish surfaces compiletest stderr artefacts
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.stderr_tail_lines is 1
    And cargo test fails with compiletest artefact "ui.stderr"
    When I invoke lading publish with that workspace
    Then the CLI exits with code 1
    And the stderr contains "Compiletest stderr artefacts"
    And the stderr contains "ui.stderr"
    And the stderr contains "line2"

  Scenario: Configured skip suppresses the pre-flight build checks
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.skip is true
    When I invoke lading publish with that workspace
    Then the publish command runs no pre-flight build commands
    And the stderr contains "the lading.toml [preflight] skip setting"

  Scenario: A skipped pre-flight still verifies the tree and the lockfiles
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And publish pre-flight probes a fresh tracked Cargo.lock with a large metadata document
    And preflight.skip is true
    When I invoke lading publish with that workspace using --forbid-dirty
    Then the publish command still verified the working tree and tracked lockfiles

  Scenario: The --skip-preflight flag suppresses the pre-flight build checks
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I run "lading publish --skip-preflight"
    Then the publish command runs no pre-flight build commands
    And the stderr contains "the --skip-preflight command-line flag"

  Scenario: LADING_SKIP_PREFLIGHT suppresses the pre-flight build checks
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the environment variable LADING_SKIP_PREFLIGHT is set to "1"
    When I invoke lading publish with that workspace
    Then the publish command runs no pre-flight build commands
    And the stderr contains "the LADING_SKIP_PREFLIGHT environment variable"

  Scenario: --no-skip-preflight reinstates a configured skip
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And preflight.skip is true
    When I run "lading publish --no-skip-preflight"
    Then the publish command runs the pre-flight cargo check and cargo test

  Scenario: The pre-flight build checks run by default
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading publish with that workspace
    Then the publish command runs the pre-flight cargo check and cargo test

  Scenario: Publish command rejects dirty workspaces with --forbid-dirty
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has uncommitted changes
    When I invoke lading publish with that workspace using --forbid-dirty
    Then the CLI exits with code 1
    And the stderr contains "Workspace has uncommitted changes; commit or stash them before publishing or re-run without --forbid-dirty."

  Scenario: Publish command allows dirty workspaces by default
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace has uncommitted changes
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"

  Scenario: Publish command removes its staged workspace copy
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    When I invoke lading publish with that workspace
    Then the publish command prints the publish plan for "alpha"
    And the staged workspace copy has been removed

  Scenario: Publish command strips all patch entries when configured
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace manifest patches crates "alpha, serde"
    And publish.strip_patches is "all"
    When I invoke lading publish with that workspace, retaining the staged copy
    Then the publish command prints the publish plan for "alpha"
    And the publish staging manifest has no patch section

  Scenario: Publish command strips patch entries per crate
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace manifest patches crates "alpha, serde"
    And publish.strip_patches is "per-crate"
    When I invoke lading publish with that workspace, retaining the staged copy
    Then the publish command prints the publish plan for "alpha"
    And the publish staging manifest omits patch entries "alpha"
    And the publish staging manifest retains patch entries "serde"

  Scenario: Publish command preserves patch entries when stripping is disabled
    Given a workspace directory with configuration
    And cargo metadata describes a sample workspace
    And the workspace manifest patches crates "alpha, serde"
    And publish.strip_patches is false
    When I invoke lading publish with that workspace, retaining the staged copy
    Then the publish command prints the publish plan for "alpha"
    And the publish staging manifest retains patch entries "alpha, serde"
