# Scenario tests

Scenario tests are asked of the user every time, as part of capturing intent. They are what the product must survive, in the user's words, and each one runs on the page against the models.

## Ask

After the intent questions, ask in one message, in plain words:

1. How it is used on a normal day, and on its worst normal day.
2. How it gets misused or abused.
3. What fails around it: power, signal, weather, people.

Propose a starter set inferred from the brief (four to eight) for the user to accept, change or add to. Mark each `"by": "user"` once the user has given or confirmed it, otherwise `"by": "inferred"`. The builder warns while every scenario is still inferred.

## Write

```json
{ "scenarios": [
  { "id": "windy", "text": "Hovers in a 10 m/s gust on a cold morning", "by": "user",
    "set": { "hover.fm": 0.5, "hover.wh": 6.3 },
    "expect": [ { "output": "hover.flight_min", "op": ">=", "value": 10 } ],
    "requires": ["endurance"],
    "events": ["button", "usb"] }
] }
```

- `set`: model inputs (`model.input`) changed for this scenario.
- `expect`: model outputs with an operator and a value. The scenario passes when every expectation holds.
- `requires`: requirement ids the scenario proves (built-in or custom).
- `events`: optional Try it actions to play while the scenario runs (button, usb, signal, battery).

## Map to EARS

| Scenario says | Requirement pattern |
|---|---|
| normal use | Always: The device shall ... |
| when something happens | Event: When ..., the device shall ... |
| during a condition | State: While ..., the device shall ... |
| misuse or failure | Unwanted: If ..., then the device shall ... |
| optional feature | Optional: Where ..., the device shall ... |

Every scenario should prove at least one requirement. A scenario with no expectation, requirement or event cannot pass or fail and the builder warns.
