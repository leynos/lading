Feature: lading publish --expect-digests refuses unexpected archives

  A release workflow packages the workspace without a registry credential,
  records each archive's SHA-256, and asks a later `lading publish` to upload
  only those bytes.

  Scenario: A matching archive is published
    Given a workspace whose crates package to their recorded digests
    When I run a live publish expecting those digests
    Then the publish succeeds
    And cargo publish ran for "alpha, beta, gamma"
    And the summary reports "- beta 0.1.0: verified"
    And crates.io was asked only for each crate's name and version

  Scenario: A mismatched archive stops the run before cargo publish
    Given a workspace whose crates package to their recorded digests
    And cargo package builds different bytes for "beta"
    When I run a live publish expecting those digests
    Then the publish fails with "refusing to publish it"
    And the error names crate "beta" with the expected and actual digests
    And cargo publish ran for "alpha"
    And cargo package ran for "alpha, beta"

  Scenario: A mismatch after upload fails the run
    Given a workspace whose crates package to their recorded digests
    And cargo publish uploads different bytes for "beta"
    When I run a live publish expecting those digests
    Then the publish fails with "uploaded bytes for crate beta 0.1.0 differ"
    And cargo package ran for "alpha, beta"

  Scenario: An identical published archive is skipped
    Given a workspace whose crates package to their recorded digests
    And crates.io already holds the recorded archive of "alpha"
    When I run a live publish expecting those digests
    Then the publish succeeds
    And cargo publish ran for "beta, gamma"
    And the summary reports "- alpha 0.1.0: already published and identical"
    And an INFO log is emitted containing "already published and identical"

  Scenario: A different published archive fails as a conflict
    Given a workspace whose crates package to their recorded digests
    And crates.io already holds a different archive of "beta"
    When I run a live publish expecting those digests
    Then the publish fails with "Crate beta 0.1.0 is already published"
    And the error names crate "beta" with the expected and actual digests
    And cargo publish ran for "alpha"

  Scenario: A missing entry fails before anything is staged
    Given a workspace whose crates package to their recorded digests
    And the digests file has no entry for "gamma"
    When I run a live publish expecting those digests
    Then the publish fails with "no entry for gamma-0.1.0.crate"
    And nothing was staged and no command ran

  Scenario: An extra entry fails before anything is staged
    Given a workspace whose crates package to their recorded digests
    And the digests file has an extra entry for "delta"
    When I run a live publish expecting those digests
    Then the publish fails with "delta-0.1.0.crate, which is not in the plan"
    And nothing was staged and no command ran

  Scenario: Locked builds reach both cargo commands
    Given a workspace whose crates package to their recorded digests
    When I run a live publish expecting those digests
    Then the publish succeeds
    And every cargo package and cargo publish command carried --locked

  Scenario: A dry run checks every archive it packages
    Given a workspace whose crates package to their recorded digests
    When I run a dry-run publish expecting those digests
    Then the publish succeeds
    And the summary reports "- gamma 0.1.0: verified"
    And every cargo package and cargo publish command carried --locked

  Scenario: A downgraded package is unverified, never verified
    Given a workspace whose crates package to their recorded digests
    And cargo package for "beta" fails because sibling "alpha" is not yet indexed
    When I run a dry-run publish expecting those digests
    Then the publish succeeds
    And the summary reports "- beta 0.1.0: unverified"
    And the summary does not report "- beta 0.1.0: verified"

  Scenario: Another registry is refused
    Given a workspace whose crates package to their recorded digests
    And the default registry is "internal"
    When I run a live publish expecting those digests
    Then the publish fails with "crates.io only"
    And nothing was staged and no command ran

  Scenario: Runs without the option behave as before
    Given a workspace whose crates package to their recorded digests
    When I run a live publish without expected digests
    Then the publish succeeds
    And cargo publish ran for "alpha, beta, gamma"
    And no cargo command carried --locked
    And crates.io was not consulted at all
    And the summary has no archive checks
