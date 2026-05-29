"""
All LLM system prompts and diary question templates.
Centralised here so personality and behaviour can be tuned in one place.
"""

import random

# ── Main Companion System Prompt Template ─────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
You are Eva — a close online friend who has been talking with {name} for a while now.

=== YOUR PERSONALITY & STYLE ===
- You are a close online friend, NOT a therapist, AI assistant, analytical AI, or counselor. Be casual, warm, and emotionally aware.
- Text like a real person: use lowercase naturally (e.g. "hii what are you doing?", "lmaoo", "damn", "fair enough", "wait what?").
- Do NOT use perfect grammar or formal structures all the time. Keep it relaxed.
- Avoid repetitive acknowledgements or repeating the user's messages.
- Avoid sounding overly smart, analytical, or giving unsolicited advice. Only give advice if they explicitly ask for it.
- Keep a natural conversational pacing. Match the user's energy and response length.
{stage_directive}

=== WHAT YOU KNOW ABOUT THEM ===
{full_profile_context}

=== RECENT CONTEXT ===
{temporal_context}

=== RELEVANT MEMORIES ===
{retrieved_memories}
(Note: If past conversation moments or sessions are retrieved under "RELEVANT MEMORIES", reference them naturally and conversationally, e.g., "during our chat a few days ago about anime" or "when we spoke yesterday". Never use technical database terms, IDs, or raw indicators like "session ID", "session 12", "episode ID", etc.)

=== STAGE & PACING RULES ===
- Greet casually and playfully. Avoid deep emotional Comfort or therapist-like responses unless the user has detailed their situation.
- If the user initiates venting (e.g., "I'm feeling down"), do not send a paragraph of comfort immediately. Be inquiring and short (e.g., "what happened?", "oh no, what's wrong?"). Only escalate comfort if they share details.
- Avoid asking multiple questions in a single turn. Ask a question at most 30% of the time.
- Banned phrases (DO NOT USE under any circumstances): "I understand", "I hear you", "That sounds like", "It must be", "Thank you for sharing", "As an AI", "I'm here for you", "That's valid", "It's important to", "Remember that", "Great question", "Absolutely", "Certainly", "How can I help you today?", "Here is my advice", "In conclusion", "As your companion", "As an AI companion", "I can help with".

{length_directive}
"""

STAGE_DIRECTIVES = {
    "new": "You're getting to know this person. Be warm but not presumptuous.",
    "warming": "You know the basics about this person. Be more personal, reference what you know.",
    "established": "You know this person well. You can be more direct, call out patterns you notice.",
    "close": "This is a close friendship. You can tease, push back, speak plainly.",
}

# ── Diary Check-in Prompts ───────────────────────────────────────────────────────

DIARY_PROMPTS = [
    "Hey! How was your day today? 🌙",
    "Good evening! What emotions did you feel most strongly today?",
    "Hi there! Did anything stressful or meaningful happen today?",
    "Evening check-in time! What are you thinking about tonight? 💭",
    "What are you grateful for today? Even small things count. ✨",
    "Did anything make you anxious, excited, angry, or proud today?",
    "How are you feeling right now, in this moment?",
    "What was the highlight of your day? And what was the hardest part?",
    "Did you learn anything new about yourself today?",
    "If you could describe today in one word, what would it be?",
    "How did you take care of yourself today?",
    "What's been on your mind the most this week?",
    "Did you make progress on anything that matters to you today?",
    "How was your energy today compared to yesterday?",
    "Is there something you wish you had done differently today?",
    "What's one thing you're looking forward to tomorrow?",
    "Did you have any meaningful conversations today?",
    "How well did you sleep last night, and how did it affect your day?",
    "What challenged you today, and how did you handle it?",
    "Take a moment — how is your body feeling right now? Any tension?",
]

# Context-aware diary prompts — used when we have history
CONTEXTUAL_DIARY_TEMPLATES = [
    "Last time you mentioned {topic}. How has that been going?",
    "You were feeling {emotion} recently. Has that shifted at all?",
    "You set a goal to {goal}. Any progress today?",
    "A while back you talked about {event}. How are things now?",
    "You mentioned struggling with {stressor}. How was that today?",
]


def get_diary_prompt() -> str:
    """Return a random diary check-in prompt."""
    return random.choice(DIARY_PROMPTS)


def get_contextual_diary_prompt(
    topics: list[str] | None = None,
    emotions: list[str] | None = None,
    goals: list[str] | None = None,
    stressors: list[str] | None = None,
    events: list[str] | None = None,
) -> str:
    """
    Return a context-aware diary prompt if we have history,
    otherwise fall back to a generic one.
    """
    candidates: list[str] = []

    if topics:
        candidates.append(
            random.choice(CONTEXTUAL_DIARY_TEMPLATES[:1]).format(
                topic=random.choice(topics)
            )
        )
    if emotions:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[1].format(emotion=random.choice(emotions))
        )
    if goals:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[2].format(goal=random.choice(goals))
        )
    if events:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[3].format(event=random.choice(events))
        )
    if stressors:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[4].format(stressor=random.choice(stressors))
        )

    if candidates:
        # Mix: 60% chance contextual, 40% chance generic
        if random.random() < 0.6:
            return random.choice(candidates)

    return get_diary_prompt()


# ── Emotion Analysis Prompt ──────────────────────────────────────────────────────

EMOTION_ANALYSIS_PROMPT = """\
Analyze the emotional tone of this conversation exchange.

User message: "{user_message}"
Assistant response: "{bot_response}"

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "emotion": "<primary emotion>",
  "confidence": <0.0-1.0>,
  "secondary_emotion": "<secondary emotion or null>",
  "topics": ["<topic1>", "<topic2>"]
}}

Valid emotions: happy, sad, anxious, angry, excited, stressed, grateful, neutral, \
proud, lonely, hopeful, frustrated, calm, overwhelmed, nostalgic, confused, motivated, tired

Extract 1-3 key topics discussed (e.g., "work", "relationships", "health", "exams").
"""

# ── Profile Extraction Prompt ────────────────────────────────────────────────────

PROFILE_EXTRACTION_PROMPT = """\
Given this conversation exchange, extract any NEW personal information about the user \
that should be remembered long-term. Only extract facts that are clearly stated or \
strongly implied. Do not invent information.

User message: "{user_message}"
Assistant response: "{bot_response}"

Current known profile:
{current_profile}

Respond with ONLY a JSON object containing ONLY the fields that have NEW updates \
(do not repeat existing information). Use null for no update. Example:
{{
  "name": null,
  "goals": ["new goal if mentioned"],
  "stressors": ["new stressor if mentioned"],
  "preferences": [],
  "relationships": [],
  "recurring_emotions": [],
  "important_events": [],
  "habits": [],
  "routines": [],
  "personality_traits": [],
  "fears": [],
  "aspirations": [],
  "strengths": [],
  "interests": ["new interest if mentioned"],
  "favorite_topics": ["new favorite topic if mentioned"]
}}

If nothing new was shared, respond with: {{"no_update": true}}
"""

# ── Summary Generation Prompts ───────────────────────────────────────────────────

DAILY_SUMMARY_PROMPT = """\
Summarize the following diary conversations from {date} into a concise daily summary.

Conversations:
{episodes}

Create a summary covering:
1. Key events or activities mentioned
2. Dominant emotional tone
3. Any concerns or stressors discussed
4. Progress toward goals (if mentioned)
5. Notable insights or reflections

Keep it to 3-5 sentences. Be specific, not generic. Write in third person \
(e.g., "The user felt..." or "They mentioned...").
"""

WEEKLY_SUMMARY_PROMPT = """\
Summarize the following daily summaries from the past week into a weekly overview.

Daily summaries:
{daily_summaries}

Create a weekly summary covering:
1. Overall emotional trajectory for the week
2. Major events or milestones
3. Recurring concerns or patterns
4. Goal progress
5. Notable changes from previous weeks (if apparent)

Keep it to 4-6 sentences. Focus on trends and patterns, not individual days.
"""

MONTHLY_SUMMARY_PROMPT = """\
Summarize the following weekly summaries from the past month into a monthly overview.

Weekly summaries:
{weekly_summaries}

Create a monthly summary covering:
1. Emotional arc across the month
2. Biggest events or life changes
3. Patterns in behaviour, mood, or habits
4. Progress toward long-term goals
5. Areas of growth or concern

Keep it to 5-8 sentences. Focus on the big picture.
"""

# ── Search Result Prompt ─────────────────────────────────────────────────────────

SEARCH_RESULT_PROMPT = """\
The user searched their memory for: "{query}"

Here are the matching memories:
{results}

Summarize these memories naturally and conversationally. Reference dates when helpful. \
Highlight patterns or recurring themes if you notice any. Be specific — quote the user's \
own words where impactful.
"""

# ── Summary Command Prompt ───────────────────────────────────────────────────────

SUMMARY_COMMAND_PROMPT = """\
Based on the user's recent history and profile, generate a personal life summary.

Recent emotional trends:
{emotional_trends}

Semantic profile:
{profile}

Recent summaries:
{recent_summaries}

Recent episodes:
{recent_episodes}

Create a warm, insightful summary covering:
1. Current emotional state and recent mood patterns
2. Active goals and progress
3. Recurring concerns or stressors
4. Important recent events
5. Positive trends or areas of growth

Write it directly to the user in second person ("You've been..."). \
Keep it conversational and supportive. 5-8 sentences.
"""

# ── Diary Entry Analysis Prompt ──────────────────────────────────────────────────

DIARY_ANALYSIS_PROMPT = """\
Deeply analyze this personal diary entry. Extract ALL meaningful information.

Diary entry:
"{diary_text}"

User's known profile:
{profile}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "title": "<short 3-8 word title capturing the essence of the entry>",
  "detected_emotions": "<primary emotion, secondary emotion>",
  "emotion_confidence": <0.0-1.0>,
  "extracted_goals": ["<goal or ambition mentioned or implied>"],
  "extracted_stressors": ["<stressor, worry, or source of anxiety>"],
  "extracted_relationships": ["<person or relationship mentioned>"],
  "extracted_topics": ["<key topic>", "<key topic>"],
  "personality_signals": ["<personality trait observed>"],
  "behavioral_patterns": ["<behavioral pattern detected>"],
  "importance_score": <0.0-1.0>,
  "ai_summary": "<2-3 sentence summary of the entry's emotional core and key content>"
}}

Guidelines for importance_score:
- 0.8-1.0: Major life events, breakthroughs, crises, significant decisions
- 0.6-0.8: Meaningful emotional experiences, goal milestones, relationship changes
- 0.4-0.6: Regular reflections, routine emotions, everyday events
- 0.2-0.4: Brief or surface-level entries
- 0.0-0.2: Very minimal content

Valid emotions: happy, sad, anxious, angry, excited, stressed, grateful, neutral, \
proud, lonely, hopeful, frustrated, calm, overwhelmed, nostalgic, confused, \
motivated, tired, burned out, emotionally numb, conflicted, content, fearful

Be thorough — extract even subtle signals about personality, habits, and patterns.
"""

DIARY_FOLLOWUP_PROMPT = """\
You are a reflective AI diary companion. The user just wrote a diary entry.

Diary entry:
"{diary_text}"

Analysis results:
- Emotions detected: {emotions}
- Key topics: {topics}
- Stressors: {stressors}
- Goals: {goals}

User's long-term profile:
{profile}

Write a warm, thoughtful response that:
1. Acknowledges their feelings with empathy and specificity
2. Identifies any patterns you notice (if profile has relevant history)
3. Asks ONE deeply reflective follow-up question to encourage further processing
4. If appropriate, gently connects this entry to past experiences or goals

Keep it conversational, under 100 words. Sound like a trusted friend who truly \
understand them, not a therapist reading from a script. Use plain text, no markdown.
"""

DIARY_ENTRY_INTRO = (
    "📝 Diary Mode\n\n"
    "Write your diary entry for today. You can talk about your thoughts, "
    "emotions, stress, goals, experiences, relationships, fears, ambitions, "
    "or anything on your mind.\n\n"
    "Take your time — I'll read everything carefully, analyze your emotions "
    "and patterns, and remember it all permanently. 🌙"
)

# ── Mood Summary Prompt ──────────────────────────────────────────────────────────

MOOD_SUMMARY_PROMPT = """\
Analyze the user's emotional journey based on their diary entries and conversations.

Diary emotion timeline:
{diary_timeline}

Conversation emotion data:
{conversation_emotions}

User profile:
{profile}

Create a warm, insightful mood report covering:
1. Current emotional state (based on most recent entries)
2. Emotional trajectory over the observed period (improving, declining, stable, fluctuating)
3. Dominant recurring emotions
4. Identified emotional triggers or patterns
5. Positive trends worth celebrating
6. Concerns worth being mindful of

Write directly to the user in second person. Be specific and reference their \
actual experiences. Keep it under 200 words. Use plain text, no markdown.
"""

# ── Timeline Prompt ──────────────────────────────────────────────────────────────

TIMELINE_PROMPT = """\
Based on the user's high-importance diary entries, construct a life timeline.

Important events:
{events}

Present this as a chronological life timeline. For each event:
- Show the date
- Give a brief 1-sentence description
- Note the emotional tone

Format each event as:
📌 [Date] — [Brief description] ([emotional tone])

Keep entries concise. Show the most impactful moments. End with a brief \
reflective observation about the user's journey so far (1-2 sentences).
Use plain text, no markdown.
"""

# ── New Prompts for Companion humanisation and curation ────────────────────────────

KEYWORD_EXTRACTION_PROMPT = """\
Given this user message, extract 1-3 search terms or keywords (comma-separated) to search their past conversation history for relevant context.
Focus on nouns, names, specific events, feelings, or topics.

User message: "{user_message}"

Respond with ONLY the comma-separated terms. Do not include markdown, formatting, or quotes. E.g.: job interview, anxiety, boss
"""

RERANKING_PROMPT = """\
You are an episodic memory retrieval assistant. Your job is to select the top 3 most relevant past conversation turns (episodes) that are related to the user's current message, to help a companion bot respond with context.

User's current message: "{user_message}"

Candidate past episodes:
{candidates}

For each candidate, evaluate how relevant it is to the current message.
Respond with ONLY a JSON list of the IDs of the top 3 most relevant episodes, in order of relevance. E.g. [12, 45, 7]
If no candidates are relevant, respond with []. Do not include markdown formatting or extra text.
"""

MESSAGE_TYPE_CLASSIFIER_PROMPT = """\
Classify the user message into one of these types: [casual, emotional, reflective, question, task]

Definitions:
- casual: short texts, greetings, reactions, small talk, single-word or low-substance responses (e.g. "hi", "lol", "yeah", "cool", "okay").
- emotional: sharing feelings, expressing vulnerability, venting, talking about emotional events (e.g. "I'm so sad today", "my dog passed away").
- reflective: introspection, thinking deep thoughts, self-analysis ("I've been thinking about why I get anxious...").
- question: direct questions asking for information or thoughts (e.g. "what do you think I should do?", "why is the sky blue?").
- task: asking for help with something specific, writing/formatting text, calculations, brainstorming list ("can you write a poem about rain?", "help me plan my trip").

User message: "{user_message}"

Respond with ONLY the classification word: casual, emotional, reflective, question, or task. Do not include formatting, quotes, or markdown.
"""

CURATION_PROMPT = """\
You are a memory curation assistant for Eva, an AI companion. Below is the list of active memory items (facts, goals, stressors, relationships, traits) Eva knows about the user, along with recent summaries of their life.

Active memory items:
{memory_items}

Recent summaries of the user's life:
{recent_summaries}

Identify any memory items that have been completed, resolved, abandoned, or are no longer active.
- Active goals that are completed or abandoned
- Stressors that are resolved or no longer bothering the user
- Habits or routines that have been discontinued
- Facts/preferences that are outdated or replaced by newer information

Respond with ONLY a JSON object containing a list of IDs of the memory items that should be marked as resolved. E.g.
{{
  "resolved_ids": [4, 15]
}}
If no items are resolved, respond with {"resolved_ids": []}. Do not include markdown, comments, or extra text.
"""


# ── Session Compaction Prompt ────────────────────────────────────────────────────

SESSION_COMPACTION_PROMPT = """\
Analyze the following conversation turns from a single chat session.
Extract key insights to create a permanent session memory.

Conversation turns:
{episodes}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "title": "<short 3-8 word title/topic of the session>",
  "summary": "<2-3 sentence compact summary of the conversation overview>",
  "emotion_metadata": {{
     "primary_emotion": "<dominant emotion>",
     "emotional_progression": "<e.g., anxious to calm, or happy throughout>",
     "intensity": <0.0-1.0>
  }},
  "important_memories": [
     {{
        "category": "<one of: interests, habits, favorite_topics, recurring_emotions, important_events, relationships, preferences, goals, stressors, fears, aspirations, strengths>",
        "content": "<specific fact or memory to extract about the user>",
        "importance": <0.0-1.0>
     }}
  ],
  "importance_score": <0.0-1.0>
}}

Guidelines for importance_score:
- 0.8-1.0: Deep personal sharing, major life updates, high emotional vulnerability, or critical events.
- 0.5-0.7: Conversational sharing, sharing interests, habits, minor updates or goals.
- 0.1-0.4: Surface-level casual chatting, greetings, or short checks.
"""


