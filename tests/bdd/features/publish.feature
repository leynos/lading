Feature: lading publish --allow-unpublished-workspace-deps flag

  Scenario: Flag is accepted in dry-run mode
    Given a valid lading workspace
    When I run "lading publish --allow-unpublished-workspace-deps"
    Then the command should not raise a preflight error about the flag

  Scenario: Flag is rejected with --live
    Given a valid lading workspace
    When I run "lading publish --allow-unpublished-workspace-deps --live"
    Then a PublishPreflightError should be raised
    And the error message should contain "--allow-unpublished-workspace-deps is only valid in dry-run mode"

  Scenario: Flag downgrades an in-plan index-lookup failure to a warning
    Given a valid lading workspace
    And a workspace where a sibling crate dependency is not yet indexed
    And the missing dependency is part of the planned publish set
    When I run "lading publish --allow-unpublished-workspace-deps"
    Then a WARNING log should be emitted containing "allow-unpublished-workspace-deps"
    And no PublishPreflightError should be raised
