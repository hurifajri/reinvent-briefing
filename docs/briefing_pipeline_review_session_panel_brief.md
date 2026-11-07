# Briefing Pipeline Review Session, Panel Brief

This is the organiser's brief for the go/no-go review of the fact-checked re:Invent Morning Briefing pipeline. Hana Kobayashi keeps it.

## Session

| Item | Detail |
|---|---|
| Date and time | Friday 14 November 2025, 10:00 PT |
| Where | Amazon Chime |
| Talk | 6 minutes, about 1 minute per slide |
| Questions | 5 minutes |
| Outcome | Priya Venkataraman records go or no-go on whether architects can use the briefing from 1 December 2025, the opening morning of re:Invent |

## Panel and their questions

Each panel member makes one call. Below each person are the questions they have said they will ask, in their own words.

### Priya Venkataraman, Principal AI/ML Solutions Architect

**Decides:** go or no-go for the briefing.

- "How many of the five items went out without an official aws.amazon.com confirmation?"
- "Can I click from any item to the page that confirmed it?"

### Kwame Asante, Senior Software Engineer, Evaluation

**Decides:** whether the safety checks and the trace audit hold up. The safety checks are the two pytest checks that must pass before the page is built. A LangSmith trace is the step-by-step record of what each agent did in a run.

- "Show me one claim the fact-checker rejected, and its LangSmith trace."
- "Which of your two safety checks fails if a claim has no source?"
- "Did your trace audit find any verdict that broke your confirm rule, and what did you change?"

### Marcus Oyelaran, Senior Solutions Architect, Enterprise Accounts

**Decides:** whether items are safe to use on customer calls.

- "Could I read an item out on a customer call as it stands?"
- "How does a rejected rumor look on the page, so I don't mistake it for a launch?"

### Hana Kobayashi, Enablement Program Manager

**Decides:** whether the briefing goes on the re:Invent week publish schedule.

- "What is left before the first live briefing on 1 December?"
- "What would you add next?"

## What the go/no-go rests on

Priya's call rests on the three project metrics below. Presenters bring their own measured figures from their repository. The panel will check the figures on the slides against GitHub during the session.

| Metric | Target | What counts |
|---|---|---|
| Unverified items published | 0 of 5 | An item published without the fact-checker confirming it against an official AWS source. Any unconfirmed claim must show as rejected. It never shows as a launch. |
| Items with an official AWS source link | 100% of confirmed items | Each confirmed item links to the aws.amazon.com page the fact-checker used to confirm the claim. |
| Runs traced in LangSmith | 100% of briefing runs | Each run records the lead agent, researcher and fact-checker steps. Each item on the page links to its own trace. |