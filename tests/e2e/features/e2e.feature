Feature: Lading end-to-end workflows in a temporary Git repository

  Scenario: Bumping versions in a non-trivial workspace marks the repo dirty
    Given a non-trivial workspace in a Git repository at version "0.1.0"
    When I run lading bump "1.0.0" in the E2E workspace
    Then the command succeeds
    And all workspace manifests are at version "1.0.0"
    And internal dependency versions are updated to "1.0.0"
    And the workspace README contains version "1.0.0"
    And the Git working tree has uncommitted changes

  Scenario: Publishing crates in dry-run mode validates the full workflow
    Given a non-trivial workspace in a Git repository at version "0.1.0"
    And cargo commands are stubbed for publish operations
    When I run lading publish --forbid-dirty in the E2E workspace
    Then the command succeeds
    And cargo preflight was run for the workspace
    And the publish order is "core, utils, app"
    And cargo package was invoked for each crate
    And cargo publish --dry-run was invoked for each crate
    And the workspace README was staged for all crates

  Scenario: Tutorial workflow bumps and publishes in dry-run mode
    Given a non-trivial workspace in a Git repository at version "0.1.0"
    And cargo commands are stubbed for publish operations
    When I run lading bump "1.0.0" in the E2E workspace
    Then the command succeeds
    And all workspace manifests are at version "1.0.0"
    And internal dependency versions are updated to "1.0.0"
    And the workspace README contains version "1.0.0"
    When I run lading publish in the E2E workspace
    Then the command succeeds
    And cargo preflight was run for the workspace
    And the publish order is "core, utils, app"
