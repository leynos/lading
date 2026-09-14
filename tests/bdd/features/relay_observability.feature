Feature: Relay observability
    The subprocess relay reports its bounded state transitions without logging
    child output.

    Scenario: Unicode fallback is observable without child output
        Given a parent relay sink that cannot encode Unicode
        When the stdout relay mirrors Unicode output
        Then one Unicode fallback relay event is emitted
        And the relay event excludes the child output
