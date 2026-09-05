Feature: lading publish --allow-unpublished-workspace-deps flag

  Scenario: Flag is accepted in dry-run mode
    Given a valid lading workspace
    When I run "lading publish --allow-unpublished-workspace-deps"
    Then the command should not raise a preflight error about the flag

  Scenario: Flag is rejected with --live
    Given a valid lading workspace
    When I run "lading publish --allow-unpublished-workspace-deps --live"
    Then a PublishPreflightError should be raised
    And the error message should contain "Unpublished workspace dependency override is only valid in dry-run mode"

  Scenario: Flag downgrades an in-plan index-lookup failure to a warning
    Given a valid lading workspace
    And a workspace where a sibling crate dependency is not yet indexed
    And the missing dependency is part of the planned publish set
    When I run "lading publish --allow-unpublished-workspace-deps"
    Then a WARNING log should be emitted containing "unpublished workspace dependency override"
    And no PublishPreflightError should be raised

  Scenario: Dry-run defaults to allowing in-plan unpublished dependencies
    Given a valid lading workspace
    And a workspace where a sibling crate dependency is not yet indexed
    And the missing dependency is part of the planned publish set
    When I run "lading publish"
    Then a WARNING log should be emitted containing "unpublished workspace dependency override"
    And no PublishPreflightError should be raised

  Scenario: Dry-run can opt out of unpublished dependency downgrades
    Given a valid lading workspace
    And a workspace where a sibling crate dependency is not yet indexed
    And the missing dependency is part of the planned publish set
    When I run "lading publish --no-allow-unpublished-workspace-deps"
    Then a PublishPreflightError should be raised
    And the error message should contain "unpublished workspace dependency override"

  Scenario: Later dependencies in publish order always fail
    Given a valid lading workspace
    And a workspace where a sibling crate dependency is not yet indexed
    And publish.order puts beta before alpha
    When I run "lading publish --allow-unpublished-workspace-deps"
    Then a PublishPreflightError should be raised
    And the error message should contain "appears after crate"

  Scenario: Dry-run progress lines name each crate's position and elapsed time
    Given a valid lading workspace
    When I run "lading publish"
    Then the publish progress lines report crates "alpha, beta, gamma" with their positions and elapsed times
    And no PublishPreflightError should be raised

  Scenario: Compiler-cache statistics are collected around the packaged builds
    Given a valid lading workspace
    And RUSTC_WRAPPER names a stub sccache
    When I run lading publish with compiler-cache statistics written to "sccache-stats.json"
    Then sccache statistics were queried before the first cargo package and after every cargo invocation
    And an INFO log should be emitted containing "Compiler cache for cargo package alpha: "
    And an INFO log should be emitted containing "Compiler cache over the publish pipeline: requests=60 hits=48 misses=12 errors=0"
    And the compiler-cache report "sccache-stats.json" lists every cargo invocation
    And no PublishPreflightError should be raised

  Scenario: Compiler-cache statistics are skipped without an sccache wrapper
    Given a valid lading workspace
    And RUSTC_WRAPPER is not set
    When I run "lading publish --sccache-stats"
    Then a WARNING log should be emitted containing "does not name an sccache binary"
    And no PublishPreflightError should be raised
