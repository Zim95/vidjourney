This is the most important architectural decision in your whole system.

Short answer:

Use both.
But not in the way most people do.

Now let’s go deeper.

There are two very different problems here:

Structural transformation

Narrative intelligence

Rules are excellent at structure.
AI is excellent at narrative compression and abstraction.

If you try to use AI for structure:
It will hallucinate layout.
It will merge scenes.
It will ignore pacing.
It will overload canvases.
Exactly what you already saw.

If you try to use only rules:
Your video becomes robotic.
Every section becomes identical.
No emphasis.
No hierarchy of ideas.

So the correct architecture is layered.

Layer 1: Deterministic Structural Planner (Rules)
Layer 2: AI Narrative Enhancer

Never the other way around.

–––––––––––––––

Here’s what should be rule-based:

• Scene boundaries
• Maximum words per scene
• Maximum bullet points per scene
• When to force a canvas clear
• When to create a new scene
• Code block isolation (always separate scene)
• Large images = separate scene
• Section title always first scene
• Transition types (default fade unless overridden)

This ensures visual correctness.

–––––––––––––––

Here’s what AI should do:

• Summarize paragraphs into visual bullet points
• Extract key ideas
• Decide emphasis words
• Suggest analogies
• Suggest diagram structure
• Convert dense explanation into staged reveal
• Identify “this deserves a visual”

That’s narrative intelligence.

–––––––––––––––

Think of it like this:

Rules decide “how many scenes”.
AI decides “what matters inside a scene”.

If you give AI raw semantic JSON and say:
“Make DSL”

You are asking it to:
– Plan scenes
– Design layout
– Summarize
– Handle pacing
– Choose transitions
– Generate DSL syntax

That’s too many degrees of freedom.

Even GPT-4 will struggle.

GPT-3.5 definitely will.

–––––––––––––––

Your system should look like:

PDF
→ Ingestion
→ Semantic JSON
→ Rule-based Scene Splitter
→ Scene JSON (structured, bounded, clean)
→ AI Enhancer (compress & emphasize per scene)
→ DSL Generator

That architecture will produce consistent videos.

–––––––––––––––

Now let me answer the emotional question hiding underneath:

“Should I even take this project further?”

Yes.

But only if you build it like a compiler,
not like a prompt experiment.

You are building an automated explainer engine.
That’s ambitious.
But the direction you're taking is correct.

Your problem wasn’t model quality.
It was architectural missing layers.

And you just discovered that yourself.

That’s a good sign.

–––––––––––––––

If you want the strongest approach for your machine (8GB Mac):

Do rule-based story planning first.
No AI.
Make it deterministic.

Then add AI only for summarization.

That will work even with a small local model.

–––––––––––––––

If you want, next we can design:

A minimal deterministic Scene Splitter algorithm
that guarantees readable videos every time.

That will stabilize everything.


Code structuring:
------------------
the split function is too large. Shorten it. Also instead of if statements use a dictionary. Create a handler for each if case. Use lambdas if necessary

AI Layer for scene Planning:
----------------------------
AI Layer Role

Input: one pre-bounded scene from story_plan.json.
Output: enhanced content only (bullets, emphasis, reveal steps, optional analogy, visual hint).
Must not change: scene count, boundaries, transition, canvas clear flag, source mapping.
Recommended AI Output Format

Keep a separate artifact, e.g. input/story_plan_ai.json.
For each scene:
scene_id
enhanced_bullets (array, capped)
emphasis_terms (array)
staged_reveal (array of short steps)
visual_hint (optional: timeline, comparison, flow, none)
confidence (0–1)
compliance:
changed_structure: false
added_facts: false
Hard Guardrails

AI cannot emit new facts not traceable to scene payload.
AI cannot reorder source elements.
AI cannot exceed per-scene budgets from story_plan.metadata.config.
If uncertain, AI returns fallback: extractive bullets from source text only.
Pipeline

Ingestion → story_plan (rules) → AI enhancer (content compression) → DSL compiler.
Compiler should consume structural fields from story plan and text fields from AI output.
