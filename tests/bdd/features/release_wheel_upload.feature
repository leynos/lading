Feature: Standalone release wheel upload through the cuprum beta
  The release workflow runs the uploader with `uv run --script`, which builds
  its environment from the script's inline metadata and lockfile rather than
  from the repository lock. That environment must hold the pinned cuprum, and
  gh must only ever be the recording stub.

  Background:
    Given a dist directory containing "lading-0.0.0-py3-none-any.whl"

  Scenario: The standalone uploader attaches a wheel with the pinned cuprum
    Given a recording gh stub that exits 0
    When the uploader runs standalone for tag "v0.0.0-stub"
    Then the uploader exits 0
    And gh was called exactly once with "release upload v0.0.0-stub" and the wheel and "--clobber"
    And the environment that ran holds the cuprum version pinned in pyproject.toml

  Scenario: A rejected upload reports gh's diagnostic
    Given a recording gh stub that writes "HTTP 422: asset exists" to stderr and exits 1
    When the uploader runs standalone for tag "v0.0.0-stub"
    Then the uploader exits 1
    And the uploader's error line contains "HTTP 422: asset exists"

  Scenario: gh inherits the environment but never credentials
    Given a recording gh stub that exits 0
    And the parent environment holds a GH_TOKEN and the sentinel "LADING_STUB_SENTINEL"
    When the uploader runs standalone for tag "v0.0.0-stub"
    Then gh saw the sentinel "LADING_STUB_SENTINEL"
    And gh saw no GH_TOKEN or GITHUB_TOKEN and an empty GH_CONFIG_DIR
    And no recorded gh call is "release create" or "release edit"
