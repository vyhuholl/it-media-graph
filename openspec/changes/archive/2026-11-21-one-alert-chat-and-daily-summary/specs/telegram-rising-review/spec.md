# Spec Delta

## MODIFIED Requirements

### Requirement: An Offer Is A Card

The bot SHALL send each offer as one message to the alert chat's Telegram topic: the channel's `@username` linked to it, its title, the families that referred to it in the window and before it, the first reference in the window, and links to up to three seed posts that referred to it in the window, one per family. Everything from a channel or a post SHALL be escaped.

#### Scenario: A card

- **GIVEN** an offer of a channel referred to by three seed families in the window and by one before it
- **WHEN** the bot sends it
- **THEN** the message names the channel by its username with a link, its title, three families in the window and one before, the date of the first reference in the window, and three links to seed posts, each of another family

#### Scenario: A title that looks like markup

- **GIVEN** a channel titled with angle brackets and an ampersand
- **WHEN** its card is sent
- **THEN** the title is shown as written, and nothing in it is read as markup

### Requirement: Offers Are Delivered Outside Quiet Hours, Uncapped

The bot SHALL send offers in rank order, to the alert chat's Telegram topic, outside its quiet hours, neither counted against the alert cap nor held for the digest. Each offer SHALL be marked delivered once sent; one that failed SHALL be tried again, and SHALL NOT be sent twice once marked.

#### Scenario: The day's selection

- **GIVEN** five offers computed at 11:00 and an alert cap already reached
- **WHEN** the bot next polls
- **THEN** the five cards are sent, in rank order, and the cap is unchanged

#### Scenario: Quiet hours

- **GIVEN** an offer not yet delivered at 03:00 Moscow time
- **WHEN** the bot polls
- **THEN** nothing is sent until the quiet hours end

#### Scenario: A failed send

- **GIVEN** a card Telegram refused to send
- **WHEN** the bot next polls
- **THEN** it is tried again, and the cards sent before it are not sent again
