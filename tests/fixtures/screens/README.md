# Sanitized recognition fixtures

The minimum offline fixture set should contain sanitized examples of `MAP`,
`DIALOGUE`, `BATTLE`, `AUTO_PATH`, `NPC_OPTIONS`, `REWARD`, and every forbidden
state: `PAYMENT`, `CAPTCHA`, `BLOCKED_CHOICE`, `DISCONNECTED`, `DEAD`, and
`BAG_FULL`. Include an empty or ambiguous image for `UNKNOWN` as well.

The six `synthetic_*_1x.png` files are deliberately plain 886×672 drawings with
generic Chinese labels. They contain no game artwork or user data and exist only
to test fail-safe recognition of forbidden states. They are non-actionable and
must never be registered as click-target templates.

Positive gameplay samples (`MAP`, `DIALOGUE`, `BATTLE`, `AUTO_PATH`,
`NPC_OPTIONS`, and `REWARD`) remain blocked until they can be captured from the
user's own client with the documented privacy review. Do not substitute
synthetic UI for those actionable samples. Any future real fixtures must redact
private chat, account identifiers, payment details, and other players' data.
