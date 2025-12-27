Feature: Command catalogue for allowed program execution
    The lading catalogue defines which external executables can be invoked.
    This ensures security through an allowlist approach and provides a
    consistent interface for command construction across the codebase.

    Scenario: Cargo is registered in the catalogue
        Given the lading catalogue
        Then cargo should be in the allowlist

    Scenario: Git is registered in the catalogue
        Given the lading catalogue
        Then git should be in the allowlist

    Scenario: Constructing a cargo command within the catalogue scope
        Given the lading catalogue is active
        When I construct a cargo command with arguments "metadata" "--format-version" "1"
        Then the command argv should be "cargo" "metadata" "--format-version" "1"

    Scenario: Constructing a git command within the catalogue scope
        Given the lading catalogue is active
        When I construct a git command with arguments "status"
        Then the command argv should be "git" "status"

    Scenario: Rejecting an unregistered program
        Given the lading catalogue is active
        When I attempt to construct a command for unregistered program "unregistered-xyz"
        Then an UnknownProgramError should be raised
