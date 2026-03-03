Building the basic DSL
-----------------------
Here is an example input
ELEMENT server TYPE square
    TEXT "SERVER"
    POSITION (0,0)
    SPAWN popup 1
    MOVE straight TO (4,2) DURATION 2 REPEAT 2 REVERSE
    REMOVE popout 1
END

This should turn into element json.

The flow is:
-------------
Text DSL -----> Lark Parser -----> AST -----> Transformer -----> SceneModel (Python dataclasses) -----> JSON, just add a to_json to the scenemodel and write that to input/renderer_instructions.json

Steps:
-------
1. We need to create a language definition. A lark file would be needed. Based on the JSON and the code. Can you create a lark file for the language syntax.


Tweaks suggested by GPT:
------------------------
1️⃣ Define Keywords as Explicit Tokens (Critical)

Problem:

IDENT can accidentally match keywords like ELEMENT, MOVE, etc.

This can cause ambiguity in LALR mode.

✅ Fix:

Define all keywords explicitly at the top of grammar:

ELEMENT: "ELEMENT"
TYPE: "TYPE"
TEXT: "TEXT"
POSITION: "POSITION"
SIZE: "SIZE"
IMAGE: "IMAGE"
FILL: "FILL"
BORDER: "BORDER"
SPAWN: "SPAWN"
IDLE: "IDLE"
MOVE: "MOVE"
REMOVE: "REMOVE"
TO: "TO"
PATH: "PATH"
DURATION: "DURATION"
REPEAT: "REPEAT"
REVERSE: "REVERSE"
SEQUENCE: "SEQUENCE"
WAIT: "WAIT"
END: "END"
CLOSE: "CLOSE"

Then update rules to use tokens:

element_block: ELEMENT IDENT TYPE IDENT element_stmt+ END

Do this for all rules.

2️⃣ Restrict Scene Structure (Cleaner v1)

Current:

start: (element_block | sequence_block)*

This allows interleaving declarations and execution.

✅ Recommended:
start: element_block* sequence_block?

This enforces:

All elements declared first

Optional single SEQUENCE block

Cleaner scene lifecycle

You can relax later if needed.

3️⃣ Allow Trailing Comma in PATH (Ergonomic Improvement)

Current:

point_list: "[" point ("," point)* "]"
✅ Improve:
point_list: "[" point ("," point)* ","? "]"

Allows:

PATH [(0,0), (1,1),]

Cleaner UX.

4️⃣ Ensure MOVE in SEQUENCE is Distinguishable

You currently have:

Definition-time:

move_stmt: MOVE IDENT move_path DURATION NUMBER repeat_clause? reverse_clause?

Execution-time:

sequence_stmt: MOVE IDENT

This is valid but your transformer must:

Distinguish between MoveDefinition

And MoveExecution

✅ Add comment for clarity:
// MOVE inside element_block defines movement configuration
// MOVE inside sequence_block triggers execution

No grammar change required — just architectural awareness.

5️⃣ Optional: Enforce At Least 2 Points in PATH

Right now, PATH allows:

PATH [(0,0)]

Which is invalid for movement.

✅ Better:

Keep grammar simple.

Enforce minimum 2 points in transformer validation instead of grammar.

Do NOT complicate grammar.

6️⃣ Optional: Make Color More Strict (Future Improvement)

Current:

color: IDENT | ESCAPED_STRING

This is flexible.

If you want stricter control later:

Define HEX_COLOR token

Or restrict allowed names

For now, keep as is.

7️⃣ Ensure NUMBER Is Consistent

You imported:

%import common.SIGNED_NUMBER -> NUMBER

That’s good.

Keep it.

Do not mix INT and NUMBER inconsistently.

You already use:

INT for repeat

NUMBER for duration

This is correct.

🎯 Final Clean Structure Recommendation

Your cleaned start rule:

start: element_block* sequence_block?

Explicit tokens declared.
Trailing comma allowed.
Transformer handles semantic validation.

🚀 Result

After these tweaks your grammar becomes:

Deterministic

LALR-safe

Production-grade

Extendable

Cleaner for error reporting

If you want next, I can give you:

A minimal models.py dataclass structure

Or a Transformer skeleton that maps parse tree → SceneModel


Lark Input:
-----------
1. Lets generate a dsl_instructions.scene in input.
2. Based on the lark file and the renderer_instructions.json.
3. Generate the equivalent instruction.
4. scene is our format of input.


fixes:
------
1. Type is shape or arrow or image.
2. shape tells you which shape.
3. For image we would have url.


Test Update:
------------
1. Just updated the test case to use image for server instead of shape.
2. Check renderer_instructions.json and update dsl_instructions.scene accordingly.

TextFormat and ImageText Update:
--------------------------------
1. We just added image text and changed the text format.
2. This section does not have to worry about text format.
3. But it has to worry about the text being added to image.
4. Looking at renderer_instructions.json, Add the support to renderer_dsl.lark and dsl_instructions.scene