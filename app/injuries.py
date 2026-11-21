"""Validate fictional physical changes and keep life-or-death outcomes player-controlled."""

import re

from app.identity import npc_ref


ACTIVE_SEVERITIES = ("none", "minor", "serious", "critical")
FIGURATIVE = re.compile(
    r"\b(?:dead\s+(?:tired|serious|quiet|wrong|set)|dying\s+(?:to|for|of\s+laughter)"
    r"|(?:died|die|dies)\s+(?:of\s+(?:embarrassment|laughter|shame)|laughing|inside)"
    r"|(?:died|die|dies|dying)\s+of\s+boredom|(?:laughed|worked)\b[^.!?]*\bto death"
    r"|broken heart|heart (?:sank|broke|skipped)|bleeding (?:money|cash)|blood (?:ran cold|boiled)"
    r"|wounded pride|hurt feelings|burn(?:s|ing) with|shot\b[^.!?]*\b(?:look|glance|smile)"
    r"|cut\b[^.!?]*\boff|pain in the neck|killed\b[^.!?]*\b(?:kindness|joke)"
    r"|bruised ego|wound up|bleeding heart|split\b[^.!?]*\bsides|knock\b[^.!?]*\bdead)\b", re.I,
)
UNCERTAIN = re.compile(
    r"\b(?:if|would|might|could|pretends? to|imagines?|dreams?|jokes?|no sign of"
    r"|(?:no|without|not) (?:injur\w*|wound\w*|bleed\w*|blood)|(?:is|was|were|did) not"
    r"|looks? as if|as though|apparently|seems? to)\b", re.I,
)
REPORTED = re.compile(r"\b(?:said|says?|told|tell(?:s|ing)?|heard|claims?|reports?)\b", re.I)
OBSERVES_SOMEONE = re.compile(
    r"\b(?:sees?|hears?|notices?|watches?|helps?|finds?|examines?|checks?|looks? at|kneels? beside)\b", re.I
)
PHYSICAL_SIGN = re.compile(
    r"\b(?:bleed\w*|blood|wound\w*|injur\w*|bruis\w*|fractur\w*|sprain\w*"
    r"|broken (?:arm|leg|rib|bone|wrist|ankle|nose)|(?:breaks?|broke) (?:his|her|their)\s+"
    r"(?:arm|leg|rib|wrist|ankle|nose)|burns?|swelling|limp\w*|dislocat\w*"
    r"|unconscious|unresponsive|faints?|passes out|shallow breath\w*|pulse"
    r"|barely breath\w*|breathing (?:stops|has stopped|falters)|stops? breathing"
    r"|life[- ]threatening|critical condition|(?:cut|cuts) (?:his|her|their)\s+"
    r"(?:arm|hand|finger|leg|face)|(?:sharp|severe|worsening) pain)\b", re.I,
)
CRITICAL_SIGN = re.compile(
    r"\b(?:life[- ]threatening|critical condition|(?:barely|hardly) breath\w*|unresponsive"
    r"|(?:faint|weak|thready|fading) pulse|pulse (?:weakens|fades|is (?:faint|weak))"
    r"|(?:breathing (?:stops|has stopped|falters)|stops? breathing)|shallow breath\w*"
    r"|no (?:detectable )?pulse|struggl\w* to breathe"
    r"|(?:heavy|profuse|uncontrolled|severe) bleeding|bleeding (?:heavily|won['’]t stop)"
    r"|los(?:es|ing) (?:a lot of|too much) blood)\b", re.I,
)
IMMEDIATE_DANGER = re.compile(
    r"\b(?:life[- ]threatening|critical condition|(?:barely|hardly) breath\w*"
    r"|breathing (?:stops|has stopped)|stops? breathing|no (?:detectable )?pulse"
    r"|(?:heavy|profuse|uncontrolled) bleeding|bleeding (?:heavily|won['’]t stop))\b", re.I,
)
RECOVERY_SIGN = re.compile(
    r"\b(?:heal\w*|recover\w*|wound\b[^.!?]*\bclosed|bleeding\b[^.!?]*\bstops?"
    r"|breathing\b[^.!?]*\bstead\w*|swelling\b[^.!?]*\bsubsides?)\b", re.I,
)
DEATH_OUTCOME = (
    r"dies|died|dead|deceased|lifeless|corpse|succumbs? to (?:his|her|their) (?:wounds|injuries)"
    r"|passes away|passed away|was killed|did not survive|(?:last|final) breath|beyond saving|cannot be revived"
)
SURVIVAL_OUTCOME = (
    r"fully recover\w*|made a full recovery|completely healed|out of danger|will survive|back to full health"
)
NONTERMINAL = re.compile(
    r"\b(?:(?:nearly|almost|not|never|looks|appears|pronounced incorrectly) dead"
    r"|near[- ]death|(?:fake|feigned) death)\b", re.I,
)


def terminal_condition(text):
    if re.fullmatch(r"\s*(?:death|fatality|killed)\s*[.!]?\s*", text, re.I):
        return True
    return any(
        re.search(rf"\b(?:{DEATH_OUTCOME})\b", NONTERMINAL.sub("", sentence), re.I)
        and not FIGURATIVE.search(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text)
    )


def figurative_only(user_message, name):
    named = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)
    targeted = [
        sentence for sentence in re.split(r"(?<=[.!?])\s+|\n", user_message)
        if named.search(sentence)
    ]
    return any(FIGURATIVE.search(sentence) for sentence in targeted) and not any(
        PHYSICAL_SIGN.search(sentence) and not FIGURATIVE.search(sentence)
        and not UNCERTAIN.search(sentence) for sentence in targeted
    )


def validate_narrated_outcomes(beats, roster, injury_statuses, user_message=""):
    figurative_targets = {
        ref for ref, status in injury_statuses.items()
        if status in ("unassessed", "none") and figurative_only(user_message, roster[ref])
    }
    for beat in beats:
        speaker, text = beat["speaker"], beat["text"]
        if injury_statuses.get(speaker) == "dead":
            raise ValueError("A deceased NPC cannot speak or act")
        if (beat["kind"] != "dialogue" and figurative_targets & {speaker, *beat["participants"]}
                and PHYSICAL_SIGN.search(text) and not FIGURATIVE.search(text)):
            raise ValueError("Figurative language does not establish an injury")
        for ref, status in injury_statuses.items():
            name = re.escape(roster[ref])
            if status == "dead":
                if beat["kind"] == "scene" and re.search(
                    rf"\b{name}\s+(?:stands|gets up|rises|speaks|wakes|awakens|returns alive)\b",
                    text, re.I,
                ):
                    raise ValueError("A deceased NPC cannot speak or act")
                continue
            outcome = DEATH_OUTCOME
            if status == "critical":
                outcome += "|" + SURVIVAL_OUTCOME
            clean = FIGURATIVE.sub(" ", NONTERMINAL.sub(" ", text))
            named_outcome = (
                rf"\b{name}\b\s+(?:(?:is|was|lies|falls?|drops?|has|takes?|breathes?)\s+)?"
                rf"(?:(?:his|her|their|a)\s+)?(?:{outcome})\b"
                rf"|\b{name}['’]s\s+(?:corpse|lifeless body|(?:last|final) breath)\b"
            )
            own_outcome = (
                rf"(?:^|[.!?,;]\s*|\band\s+)(?:(?:he|she|they|I)\s+(?:(?:am|is|are|was|has|had)\s+)?)?"
                rf"(?:(?:falls?|drops?|lies|breathes?|takes?|collapses|slumps)\s+(?:(?:his|her|their|a)\s+)?)?"
                rf"(?:{outcome})\b"
            )
            affected = {item for item in beat["participants"] if item in injury_statuses}
            if re.search(named_outcome, clean, re.I) or (
                (ref == speaker or (beat["kind"] == "scene" and affected == {ref}))
                and re.search(own_outcome, clean, re.I)
            ):
                raise ValueError("Only the player can resolve an NPC's survival or death")


def validate_physical_change(proposal, state, user_message, beats, character_names=()):
    severity = proposal.get("injury_status")
    evidence = proposal.get("evidence")
    source = proposal.get("source")
    previous = state.get("injury_status") or "unassessed"
    condition = proposal["proposed_value"]
    if (severity not in ACTIVE_SEVERITIES or previous == "dead"
            or (previous == "critical" and severity != "critical")
            or not isinstance(evidence, str) or not 3 <= len(evidence.strip()) <= 320
            or (source != "user" and type(source) is not int)
            or terminal_condition(condition) or FIGURATIVE.search(condition)):
        return None
    npc = npc_ref(state["character_id"])
    if previous in ("unassessed", "none") and figurative_only(user_message, state["name"]):
        return None
    sources = [("user", user_message, None)]
    sources.extend(
        (index, beat["text"], beat["speaker"]) for index, beat in enumerate(beats)
        if beat["kind"] != "dialogue" and "player" in {*beat["participants"], *beat["witnesses"]}
        and (npc == beat["speaker"] or npc in beat["participants"])
    )
    name = state["name"]
    sign = RECOVERY_SIGN if severity == "none" else PHYSICAL_SIGN
    matches = {}
    for ref, text, actor in sources:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            if (evidence.strip() not in sentence
                    or (ref == "user" and REPORTED.search(sentence))
                    or FIGURATIVE.search(sentence) or UNCERTAIN.search(sentence)
                    or terminal_condition(sentence)):
                continue
            symptom = sign.search(sentence)
            if symptom is None:
                continue
            # An action's implicit subject is its actor. A different patient must
            # be identified explicitly, and only one patient may be inferred.
            other_patient = any(
                explicit_patient(sentence, other, symptom.start())
                for other in character_names if other != name
            )
            if other_patient or (actor != npc and not explicit_patient(sentence, name, symptom.start())):
                continue
            if severity == "critical" and previous != "critical" and not CRITICAL_SIGN.search(sentence):
                continue
            if previous == "serious" and severity == "minor" and not RECOVERY_SIGN.search(sentence):
                continue
            matches[ref] = sentence
            break
    if source not in matches:
        if len(matches) != 1:
            return None
        source = next(iter(matches))
    if severity == "none" and previous in ("unassessed", "none"):
        return None
    if severity != "none" and IMMEDIATE_DANGER.search(matches[source]):
        severity = "critical"
    return {
        "injury_status": severity,
        "old_injury_status": previous,
        "evidence": evidence.strip(),
        "source_beat": None if source == "user" else source,
    }


def explicit_patient(sentence, name, symptom_start):
    named = rf"(?<!\w){re.escape(name)}(?!\w)"
    # Keep the subject, a possessive, or an explicit relative clause. A name
    # merely mentioned in someone else's action is insufficient evidence.
    pattern = rf"{named}(?:['’]s\s+|\s*,\s*who(?:se)?\s+|\s+)"
    for match in re.finditer(pattern, sentence, re.I):
        if match.end() <= symptom_start and (
            match.start() == len(sentence) - len(sentence.lstrip())
            or re.search(r"['’]s\s*$|,\s*who(?:se)?\s*$", match.group(), re.I)
            or re.match(r"(?:is|was|has|lies|suffers?|is now)\b", sentence[match.end():], re.I)
        ):
            return True
    return False


def detect_immediate_danger(states, user_message, beats):
    """Recognize explicit danger even if the state analyzer understates it."""
    states = [dict(state) for state in states]
    names = {
        state["name"]: re.compile(rf"(?<!\w){re.escape(state['name'])}(?!\w)", re.I)
        for state in states
    }
    result = []
    for state in states:
        previous = state["injury_status"]
        if previous in ("critical", "dead"):
            continue
        name = state["name"]
        npc = npc_ref(state["character_id"])
        sources = [("user", user_message)]
        sources.extend(
            (index, beat["text"]) for index, beat in enumerate(beats)
            if beat["kind"] == "action" and beat["speaker"] == npc
            and "player" in {*beat["participants"], *beat["witnesses"]}
        )
        for source, text in sources:
            evidence = next((
                sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+|\n", text)
                if len(sentence.strip()) <= 320 and (danger := IMMEDIATE_DANGER.search(sentence))
                and not OBSERVES_SOMEONE.search(sentence[:danger.start()])
                and not re.search(r"\bnot\s*$", sentence[:danger.start()], re.I)
                and not re.search(r"\b(?:I|you|we)\b", sentence[:danger.start()], re.I)
                and not any(pattern.search(sentence) for other, pattern in names.items() if other != name)
                and (source != "user" or names[name].match(sentence.lstrip()))
            ), "")
            if not evidence:
                continue
            proposal = {
                "character_name": name,
                "field_name": "physical_condition",
                "old_value": state["physical_condition"] or "",
                "proposed_value": evidence,
                "reason": "The exchange establishes an immediate danger to life.",
                "injury_status": "critical",
                "source": source,
                "evidence": evidence,
            }
            validated = validate_physical_change(proposal, state, user_message, beats, names)
            if validated is not None:
                result.append({**proposal, **validated})
                break
    return result


def apply_physical_change(connection, campaign_id, character_id, proposal,
                          user_message_id, assistant_message_id):
    severity = proposal["injury_status"]
    updated = connection.execute("""
        UPDATE character_states
        SET physical_condition = ?, injury_status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE character_id = ? AND COALESCE(physical_condition, '') = ? AND injury_status = ?
          AND injury_status <> 'dead' AND (injury_status <> 'critical' OR ? = 'critical')
    """, (
        proposal["proposed_value"], severity, character_id, proposal["old_value"],
        proposal["old_injury_status"], severity,
    )).rowcount
    if updated and severity == "critical" and proposal["old_injury_status"] != "critical":
        beat = proposal["source_beat"]
        connection.execute("""
            INSERT INTO critical_injury_decisions
                (campaign_id, character_id, condition, evidence, source_message_id, source_beat)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            campaign_id, character_id, proposal["proposed_value"], proposal["evidence"],
            user_message_id if beat is None else assistant_message_id, beat,
        ))
    return bool(updated)


def pending_injuries(connection, campaign_id):
    return [dict(row) for row in connection.execute("""
        SELECT decision.*, npc.name AS character_name, state.physical_condition AS current_condition
        FROM critical_injury_decisions decision
        JOIN characters npc ON npc.id = decision.character_id
        JOIN character_states state ON state.character_id = npc.id
        WHERE decision.campaign_id = ? AND decision.outcome = 'pending'
        ORDER BY decision.id
    """, (campaign_id,))]
