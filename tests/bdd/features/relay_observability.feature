Feature: Relay observability
    The subprocess relay reports its bounded state transitions without logging
    child output.

    Scenario: Unicode fallback is observable without child output
        Given a valid lading workspace
        When the CLI relays UTF-8 cargo output through a cp1252 text stream
        Then the CLI emits one Unicode fallback relay event for stdout
        And the CLI preserves the exact child output
        And the relay event excludes the child output
